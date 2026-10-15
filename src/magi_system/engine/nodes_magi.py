"""Subgraph B — MAGI analysis and decision (brief §7, §8).

Stages (each a fan-out/fan-in with round-scoped keys "stage:r{round}:{agent}"):
  analysis  → three independent read-only analyses
  proposal  → three independent proposals (or revisions)
  review    → masked cross-review by MAGI + preventive review by QA/SRE/Security/Tech Writer
then tool-based verification of claims, admissibility, ranking and selection in code.
"""

from __future__ import annotations

import hashlib

from magi_system.agents.caller import untrusted
from magi_system.agents.profiles import PROFILES
from magi_system.decision import selection
from magi_system.engine.context import RunContext, node
from magi_system.engine.helpers import (
    assumptions_from_drafts,
    claim_status_map,
    claims_from_drafts,
    collect_stage,
    dump,
    failure_result,
    findings_from_drafts,
    jdump,
    masked_proposal,
    meta,
    pending_agents,
    policy_block,
    repo_block,
    request_block,
    requirements_block,
    resolve_assumptions,
    sends,
    settle_findings,
    verify_claims,
)
from magi_system.engine.schemas import AnalysisDraft, CrossReviewDraft, PreventiveReviewDraft, ProposalDraft
from magi_system.models.common import MAGI_IDS, AgentId, Phase, stable_id
from magi_system.models.decision import (
    AgentFailure,
    ArchitectAnalysis,
    ArchitectureProposal,
    CriterionScore,
    Finding,
    PlannedChange,
    ProposalAssessment,
    RequirementCoverage,
    SelectionDecision,
    SelectionOutcome,
)
from magi_system.models.knowledge import Claim, Evidence, EvidenceKind, Requirement, RequirementKind
from magi_system.policy.engine import PolicyError, matches_any, normalize_rel_path

SPECIALISTS = (AgentId.QA, AgentId.SRE, AgentId.SECURITY, AgentId.TECH_WRITER)
REVIEWERS = MAGI_IDS + SPECIALISTS


def _scope(state: dict) -> str:
    return f"r{state.get('architecture_round', 0)}"


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _ok_or_fail(key: str, result) -> dict:
    if isinstance(result, AgentFailure):
        return {"stage_results": {key: failure_result(result)}}
    return {"stage_results": {key: {"ok": True, "draft": result.model_dump(mode="json")}}}


def _round_proposals(state: dict, round: int) -> list[ArchitectureProposal]:
    props = [ArchitectureProposal.model_validate(p) for p in (state.get("proposals") or {}).values()]
    return sorted((p for p in props if p.meta.round == round), key=lambda p: p.id)


# -- begin ------------------------------------------------------------------------------------


@node("magi_begin")
def magi_begin(state: dict, ctx: RunContext) -> dict:
    ctx.workspaces.restore(ctx.run_id, state["baseline_commit"])  # design facts refer to the baseline
    ctx.events.emit("magi_round", round=state.get("architecture_round", 0), mode=state.get("magi_mode", "analyze"))
    return {"selected_plan": None}


def route_begin(state: dict):
    if state.get("magi_mode") == "revise":
        return sends(state, "magi_propose", "proposal", _scope(state), pending_agents(state, "proposal", _scope(state), MAGI_IDS))
    return sends(state, "magi_analyze", "analysis", _scope(state), pending_agents(state, "analysis", _scope(state), MAGI_IDS))


# -- analysis ---------------------------------------------------------------------------------


@node("magi_analyze")
def magi_analyze(state: dict, ctx: RunContext) -> dict:
    agent, key = AgentId(state["fanout"]["agent"]), state["fanout"]["key"]
    profile = PROFILES[agent]
    result = ctx.caller.run_tools(
        agent, purpose="analysis", round=state["architecture_round"], prompt_name="magi_analysis",
        prompt_values={"display_name": profile.display_name, "perspective": profile.perspective},
        body="\n\n".join([request_block(state), policy_block(ctx), repo_block(state)]),
        final_schema=AnalysisDraft, toolbox=ctx.toolbox(agent, with_runner=False), phase=Phase.MAGI_ANALYSIS.value,
    )
    return _ok_or_fail(key, result)


@node("magi_analysis_collect")
def magi_analysis_collect(state: dict, ctx: RunContext) -> dict:
    round, scope = state["architecture_round"], _scope(state)
    decision, update, ok, failed = collect_stage(state, ctx, "analysis", scope, MAGI_IDS, quorum=ctx.policy.min_valid_magi_results)
    if decision != "ok":
        return update
    request_text = state["original_request"]["text"]
    norm_request = _norm(request_text)
    requirements: dict[str, Requirement] = {}
    evidence: list[Evidence] = []
    analyses, all_claims = [], []
    for agent in MAGI_IDS:
        if agent not in ok:
            continue
        draft = AnalysisDraft.model_validate(ok[agent]["draft"])
        owner = f"analysis:{round}:{agent.value}"
        claims = claims_from_drafts(draft.claims, owner)
        all_claims += claims
        req_ids = []
        for rd in draft.requirements:
            if rd.kind == "explicit":
                quote = _norm(rd.source_quote)
                if not quote or quote not in norm_request:
                    ctx.events.emit("requirement_rejected", agent=agent.value, text=rd.text, reason="source_quote not found in request")
                    continue
                rid = stable_id("req", "explicit", quote)
                requirements.setdefault(rid, Requirement(
                    meta=meta("req", agent, round, Phase.MAGI_ANALYSIS, quote).model_copy(update={"id": rid}),
                    kind=RequirementKind.EXPLICIT, text=rd.text, mandatory=rd.mandatory, source_quote=rd.source_quote,
                ))
            else:
                quotes = [_norm(q) for q in rd.derived_from_quotes if _norm(q)]
                if not quotes or any(q not in norm_request for q in quotes):
                    ctx.events.emit("requirement_rejected", agent=agent.value, text=rd.text,
                                    reason="derived requirement not traceable to the request; kept out of requirements")
                    continue
                refs = []
                for q in quotes:
                    ev = Evidence(
                        meta=meta("ev", AgentId.PLATFORM, round, Phase.MAGI_ANALYSIS, "request-quote", q, input_hash=state["original_request"]["sha256"]),
                        kind=EvidenceKind.DOCUMENT, locator="request", summary=q, content_hash=state["original_request"]["sha256"],
                        target_commit=state["baseline_commit"], collected_by_tool="request-quote-check",
                    )
                    evidence.append(ev)
                    refs.append(ev.id)
                rid = stable_id("req", "derived", _norm(rd.text))
                # Interpretations are never mandatory: only explicit text from the request binds a proposal.
                requirements.setdefault(rid, Requirement(
                    meta=meta("req", agent, round, Phase.MAGI_ANALYSIS, rd.text).model_copy(update={"id": rid}),
                    kind=RequirementKind.DERIVED, text=rd.text, mandatory=False, derived_from=tuple(refs),
                ))
            req_ids.append(rid)
        analyses.append(ArchitectAnalysis(
            meta=meta("ana", agent, round, Phase.MAGI_ANALYSIS, input_hash=state["baseline_commit"]),
            understanding=draft.understanding, requirement_ids=tuple(req_ids), claims=tuple(claims),
            assumptions=tuple(assumptions_from_drafts(draft.assumptions, claims, owner)),
            open_questions=tuple(draft.open_questions), risks=tuple(draft.risks),
        ))
    # Conflicts and factual gaps: claims on the same target with opposite expectations.
    by_target: dict[tuple, set] = {}
    for c in all_claims:
        by_target.setdefault((c.kind.value, c.target, c.pattern), set()).add(c.expect)
    for target, expects in by_target.items():
        if len(expects) > 1:
            ctx.events.emit("magi_conflict", claim_kind=target[0], target=target[1], detail="architects disagree; resolved by tool verification")
    # Targeted evidence gathering: verify every analysis claim with tools.
    verified, results = verify_claims(ctx, all_claims, commit=state["baseline_commit"], round=round)
    upd = {
        "analyses": dump(analyses),
        "claims": {cid: c.model_dump(mode="json") for cid, c in verified.items()},
        "verification_results": dump(results.values()),
        "evidence": dump(evidence),
        "requirements": dump(requirements.values()),
    }
    if failed:
        ctx.events.emit("quorum_proceed", stage="analysis", failed=sorted(a.value for a in failed), quorum=ctx.policy.min_valid_magi_results)
    known = {**(state.get("requirements") or {}), **upd["requirements"]}
    if not any(r["mandatory"] and r["kind"] == "explicit" for r in known.values()):
        upd |= {"final_status": "UNRESOLVED_REQUIREMENT", "diagnosis": "the request yields no traceable mandatory requirement"}
    return upd


def route_analysis(state: dict):
    if state.get("final_status"):
        return "__end__"
    retry = [a for a in MAGI_IDS if (state.get("stage_results") or {}).get(f"analysis:{_scope(state)}:{a.value}", {}).get("retry")]
    if retry:
        return sends(state, "magi_analyze", "analysis", _scope(state), retry)
    return sends(state, "magi_propose", "proposal", _scope(state), MAGI_IDS)


# -- proposals --------------------------------------------------------------------------------


def _facts_block(state: dict) -> str:
    facts = [
        {"claim": c["statement"], "target": c["target"], "status": c["status"]}
        for c in (state.get("claims") or {}).values()
        if c["id"].startswith("clm-")
    ]
    return "## Verified facts (tool-checked)\n" + untrusted(sorted(facts, key=lambda f: f["target"]))


def _revision_block(state: dict, agent: AgentId) -> str:
    prev_round = state["architecture_round"] - 1
    own = [p for p in _round_proposals(state, prev_round) if p.author == agent]
    findings = [f for f in (state.get("findings") or {}).values() if own and f["subject_id"] == own[0].id]
    decision = (state.get("decisions") or {}).get(f"selection:r{prev_round}", {})
    escalations = [state["findings"][fid] for fid in (state.get("remediation") or {}).get("finding_ids", []) if fid in state.get("findings", {})]
    return "## Revision context\n" + untrusted({
        "your_previous_proposal": masked_proposal(own[0].model_dump(mode="json")) if own else None,
        "verified_findings_on_it": [{k: f[k] for k in ("title", "severity", "status", "status_reason", "detail")} for f in findings],
        "standings": decision.get("standings", []),
        "escalations_from_implementation": [{k: f[k] for k in ("title", "category", "severity", "status", "detail")} for f in escalations],
    })


@node("magi_propose")
def magi_propose(state: dict, ctx: RunContext) -> dict:
    agent, key = AgentId(state["fanout"]["agent"]), state["fanout"]["key"]
    profile = PROFILES[agent]
    parts = [request_block(state), policy_block(ctx), repo_block(state), requirements_block(state), _facts_block(state)]
    if state.get("magi_mode") == "revise":
        parts.append(_revision_block(state, agent))
    result = ctx.caller.run_tools(
        agent, purpose="proposal", round=state["architecture_round"], prompt_name="magi_proposal",
        prompt_values={"display_name": profile.display_name, "perspective": profile.perspective},
        body="\n\n".join(parts), final_schema=ProposalDraft, toolbox=ctx.toolbox(agent, with_runner=False),
        phase=Phase.MAGI_PROPOSAL.value,
    )
    return _ok_or_fail(key, result)


def _labels(proposals: list[ArchitectureProposal]) -> dict[str, str]:
    order = sorted(proposals, key=lambda p: hashlib.sha256(p.id.encode()).hexdigest())
    return {p.id: f"Proposal {chr(65 + i)}" for i, p in enumerate(order)}


@node("magi_proposal_collect")
def magi_proposal_collect(state: dict, ctx: RunContext) -> dict:
    round, scope = state["architecture_round"], _scope(state)
    decision, update, ok, failed = collect_stage(state, ctx, "proposal", scope, MAGI_IDS, quorum=ctx.policy.min_valid_magi_results)
    if decision != "ok":
        return update
    previous = {p.author: p for p in _round_proposals(state, round - 1)} if round > 0 else {}
    proposals = []
    for agent in MAGI_IDS:
        if agent not in ok:
            continue
        d = ProposalDraft.model_validate(ok[agent]["draft"])
        prev = previous.get(agent)
        version = prev.version + 1 if prev else 1
        owner = f"proposal:{round}:{agent.value}:v{version}"
        claims = claims_from_drafts(d.claims, owner)
        prop_meta = meta("prop", agent, round, Phase.MAGI_PROPOSAL, version, input_hash=state["baseline_commit"],
                         evidence_refs=[c.id for c in claims])
        proposals.append(ArchitectureProposal(
            meta=prop_meta, author=agent, version=version, title=d.title, summary=d.summary,
            coverage=tuple(RequirementCoverage(requirement_id=c.requirement_id, covered=c.covered, how=c.how) for c in d.coverage),
            claims=tuple(claims), assumptions=tuple(assumptions_from_drafts(d.assumptions, claims, owner)),
            components=tuple(d.components), contracts=tuple(d.contracts),
            changes=tuple(PlannedChange(path=c.path, action=c.action, description=c.description) for c in d.changes),
            verification_plan=tuple(d.verification_plan), migration_rollback=d.migration_rollback,
            documentation_impact=tuple(d.documentation_impact), tradeoffs=tuple(d.tradeoffs),
            post_implementation_obligations=tuple(d.post_implementation_obligations),
            derived_from=(prev.id,) if prev else (),
        ))
    labels = _labels(proposals)
    for p in proposals:
        ctx.events.emit("proposal", id=p.id, author=p.author.value, version=p.version, label=labels[p.id], round=round)
    return {"proposals": dump(proposals), "masked_labels": {f"{scope}:{pid}": label for pid, label in labels.items()}}


def route_proposal(state: dict):
    if state.get("final_status"):
        return "__end__"
    scope = _scope(state)
    retry = [a for a in MAGI_IDS if (state.get("stage_results") or {}).get(f"proposal:{scope}:{a.value}", {}).get("retry")]
    if retry:
        return sends(state, "magi_propose", "proposal", scope, retry)
    return sends(state, "magi_review", "review", scope, REVIEWERS)


# -- reviews ----------------------------------------------------------------------------------


def _visible(state: dict, reviewer: AgentId) -> list[tuple[str, dict]]:
    """Masked proposals visible to a reviewer (never its own)."""
    scope = _scope(state)
    out = []
    for p in _round_proposals(state, state["architecture_round"]):
        if p.author == reviewer:
            continue
        out.append((state["masked_labels"][f"{scope}:{p.id}"], masked_proposal(p.model_dump(mode="json"))))
    return sorted(out)


@node("magi_review")
def magi_review(state: dict, ctx: RunContext) -> dict:
    agent, key = AgentId(state["fanout"]["agent"]), state["fanout"]["key"]
    profile = PROFILES[agent]
    shown = {label: p for label, p in _visible(state, agent)}
    body = "\n\n".join([request_block(state), policy_block(ctx), requirements_block(state), _facts_block(state),
                        "## Proposals (author masked)\n" + untrusted(shown)])
    if agent in MAGI_IDS:
        criteria = ", ".join(ctx.policy.effective["decision"]["rubric"])
        result = ctx.caller.ask(
            agent, purpose="cross_review", round=state["architecture_round"], prompt_name="magi_cross_review",
            prompt_values={"display_name": profile.display_name, "perspective": profile.perspective, "criteria": criteria},
            body=body, schema=CrossReviewDraft, phase=Phase.MAGI_CROSS_REVIEW.value,
        )
    else:
        result = ctx.caller.ask(
            agent, purpose="preventive_review", round=state["architecture_round"], prompt_name="preventive_review",
            prompt_values={"display_name": profile.display_name, "perspective": profile.perspective},
            body=body, schema=PreventiveReviewDraft, phase=Phase.PREVENTIVE_REVIEW.value,
        )
    upd = _ok_or_fail(key, result)
    if not isinstance(result, AgentFailure):
        upd["stage_results"][key]["shown"] = sorted(shown)
    return upd


@node("magi_review_collect")
def magi_review_collect(state: dict, ctx: RunContext) -> dict:
    round, scope = state["architecture_round"], _scope(state)
    d1, u1, ok_magi, _ = collect_stage(state, ctx, "review", scope, MAGI_IDS, quorum=ctx.policy.min_valid_magi_results)
    d2, u2, ok_spec, _ = collect_stage(state, ctx, "review", scope, SPECIALISTS)
    if "retry" in (d1, d2):
        merged = {"technical_retries": {**u1.get("technical_retries", {}), **u2.get("technical_retries", {})},
                  "stage_results": {**u1.get("stage_results", {}), **u2.get("stage_results", {})}}
        return merged
    if d1 == "terminal":
        return u1
    if d2 == "terminal":
        return u2
    by_label = {label: pid for pid, label in ((k.split(":", 1)[1], v) for k, v in state["masked_labels"].items() if k.startswith(f"{scope}:"))}
    authors = {p.id: p.author for p in _round_proposals(state, round)}
    rubric = ctx.policy.effective["decision"]["rubric"]
    assessments, findings, scenarios, impact = [], [], {}, []
    for agent, r in ok_magi.items():
        draft = CrossReviewDraft.model_validate(r["draft"])
        for a in draft.assessments:
            pid = by_label.get(a.label)
            if pid is None or a.label not in r.get("shown", []) or authors.get(pid) == agent:
                ctx.events.emit("assessment_rejected", reviewer=agent.value, label=a.label, reason="unknown, unseen or own proposal")
                continue
            fs = findings_from_drafts(a.findings, producer=agent, round=round, phase=Phase.MAGI_CROSS_REVIEW, subject_id=pid)
            findings += fs
            assessments.append(ProposalAssessment(
                meta=meta("asmt", agent, round, Phase.MAGI_CROSS_REVIEW, pid),
                reviewer=agent, proposal_id=pid, masked_label=a.label, supports=a.supports, finding_ids=tuple(f.id for f in fs),
                scores=tuple(CriterionScore(criterion=s.criterion, score=s.score, rationale=s.rationale) for s in a.scores if s.criterion in rubric),
            ))
    for agent, r in ok_spec.items():
        draft = PreventiveReviewDraft.model_validate(r["draft"])
        for rev in draft.reviews:
            pid = by_label.get(rev.label)
            if pid is None:
                continue
            findings += findings_from_drafts(rev.findings, producer=agent, round=round, phase=Phase.PREVENTIVE_REVIEW, subject_id=pid)
        if agent is AgentId.QA:
            for s in draft.acceptance_scenarios:
                sid = stable_id("acc", round, s.id, s.command)
                decision = ctx.policy.check_command(s.command) if s.command else None
                scenarios[sid] = {
                    "id": sid, "round": round, "name": s.id, "description": s.description,
                    "requirement_ids": s.requirement_ids, "command": s.command,
                    "denied": bool(decision and not decision.allowed), "denial_reason": decision.reason if decision and not decision.allowed else "",
                }
        if agent is AgentId.TECH_WRITER:
            impact = list(draft.documentation_impact)
        if agent is AgentId.SRE and draft.not_applicable_reason:
            ctx.events.emit("sre_not_applicable_claim", round=round, reason=draft.not_applicable_reason)
    upd = {"assessments": dump(assessments), "findings": dump(findings), "acceptance_scenarios": scenarios}
    if impact:
        upd["documentation_results"] = {f"impact:r{round}": {"round": round, "impact": impact}}
    return upd


def route_review(state: dict):
    if state.get("final_status"):
        return "__end__"
    scope = _scope(state)
    retry = [a for a in REVIEWERS if (state.get("stage_results") or {}).get(f"review:{scope}:{a.value}", {}).get("retry")]
    if retry:
        return sends(state, "magi_review", "review", scope, retry)
    return "magi_verify"


# -- verification & selection -----------------------------------------------------------------


@node("magi_verify")
def magi_verify(state: dict, ctx: RunContext) -> dict:
    """Verify decisive claims with tools; settle findings; resolve assumptions."""
    round = state["architecture_round"]
    proposals = _round_proposals(state, round)
    findings = [Finding.model_validate(f) for f in state["findings"].values() if f["meta"]["round"] == round and f["meta"]["phase"] in (Phase.MAGI_CROSS_REVIEW.value, Phase.PREVENTIVE_REVIEW.value)]
    claims: list[Claim] = [c for p in proposals for c in p.claims] + [c for f in findings for c in f.claims]
    known = claim_status_map(state)
    verified, results = verify_claims(ctx, [c for c in claims if c.id not in known], commit=state["baseline_commit"], round=round)
    status = {**known, **{cid: c.status for cid, c in verified.items()}}
    settled = settle_findings(findings, status, ctx.events)
    updated = []
    for p in proposals:
        new_claims = tuple(c.model_copy(update={"status": status.get(c.id, c.status)}) for c in p.claims)
        updated.append(p.model_copy(update={"claims": new_claims, "assumptions": tuple(resolve_assumptions(list(p.assumptions), status))}))
    return {
        "claims": {cid: c.model_dump(mode="json") for cid, c in verified.items()},
        "verification_results": dump(results.values()),
        "findings": dump(settled),
        "proposals": dump(updated),
    }


def _paths_ok(p: ArchitectureProposal, ctx: RunContext) -> tuple[bool, list[str]]:
    problems = []
    for ch in p.changes:
        try:
            rel = normalize_rel_path(ch.path)
        except PolicyError as exc:
            problems.append(str(exc))
            continue
        if matches_any(rel, ctx.policy.protected_paths):
            problems.append(f"protected path: {rel}")
        if ctx.project.config.write_scopes and not matches_any(rel, ctx.project.config.write_scopes) and not matches_any(rel, ctx.doc_scopes):
            problems.append(f"outside project write scopes: {rel}")
        if ch.action == "delete" and matches_any(rel, ctx.test_scopes):
            problems.append(f"deletes tests: {rel}")
    return not problems, problems


def _classify_no_admissible(standings) -> str:
    reasons = [r for s in standings for r in s.reasons]
    if standings and all(any(r.startswith("policy:") for r in s.reasons) for s in standings):
        return "POLICY_DENIED"
    if any(r.startswith(("critical unknown unresolved", "critical claim unresolved", "mandatory requirement not covered")) for r in reasons):
        return "UNRESOLVED_REQUIREMENT"
    return "FAILED_VERIFICATION"


@node("magi_select")
def magi_select(state: dict, ctx: RunContext) -> dict:
    round = state["architecture_round"]
    decision_cfg = ctx.policy.effective["decision"]
    proposals = _round_proposals(state, round)
    requirements = [Requirement.model_validate(r) for r in state["requirements"].values()]
    findings = [Finding.model_validate(f) for f in state["findings"].values() if f["meta"]["round"] == round]
    assessments = [ProposalAssessment.model_validate(a) for a in state["assessments"].values() if a["meta"]["round"] == round]
    path_ok, path_reasons = {}, {}
    for p in proposals:
        path_ok[p.id], path_reasons[p.id] = _paths_ok(p, ctx)
    status = claim_status_map(state)
    upd: dict = {}
    for _attempt in range(2):
        outcome, winner, standings, tie = selection.rank(
            proposals, requirements=requirements, findings=findings, assessments=assessments, claim_status=status,
            rubric=decision_cfg["rubric"], tie_break=decision_cfg["tie_break"], tie_epsilon=decision_cfg["tie_epsilon"], path_ok=path_ok,
        )
        disputed = selection.disputed_claims_affecting_winner(winner, proposals, findings, status) if winner else []
        if not disputed:
            break
        # A disputed fact could change the winner: verify it before deciding.
        to_verify = [c for p in proposals for c in p.claims if c.id in disputed] + [c for f in findings for c in f.claims if c.id in disputed]
        verified, results = verify_claims(ctx, to_verify, commit=state["baseline_commit"], round=round)
        status.update({cid: c.status for cid, c in verified.items()})
        findings = settle_findings(findings, status, ctx.events)
        upd.setdefault("claims", {}).update({cid: c.model_dump(mode="json") for cid, c in verified.items()})
        upd.setdefault("verification_results", {}).update(dump(results.values()))
        upd["findings"] = dump(findings)
    standings = [
        s.model_copy(update={"reasons": s.reasons + tuple(f"policy: {r}" for r in path_reasons[s.proposal_id])}) if path_reasons[s.proposal_id] else s
        for s in standings
    ]
    max_rounds = ctx.run_input.budget.max_architecture_rounds
    if outcome is SelectionOutcome.SELECTED:
        rationale = f"selected {winner} by peer rubric among admissible proposals"
    elif round + 1 < max_rounds:
        outcome, rationale = SelectionOutcome.REVISE, f"no admissible proposal in round {round}; bounded revision {round + 1}/{max_rounds - 1}"
    else:
        rationale = f"no admissible proposal after {round + 1} architecture round(s)"
    decision = SelectionDecision(
        meta=meta("sel", AgentId.PLATFORM, round, Phase.SELECTION, plan_version=state.get("plan_version", 0) + (1 if winner else 0)),
        outcome=outcome, selected_proposal_id=winner, standings=tuple(standings), tie_break_applied=tie, rationale=rationale,
    )
    ctx.events.emit("selection", round=round, outcome=outcome.value, selected=winner, tie_break=tie,
                    standings=[s.model_dump(mode="json") for s in standings])
    upd["decisions"] = {f"selection:r{round}": decision.model_dump(mode="json")}
    if outcome is SelectionOutcome.SELECTED:
        upd |= {"selected_plan": winner, "plan_version": state.get("plan_version", 0) + 1, "magi_mode": "analyze",
                "routing": [{"from": "magi", "to": "implementation", "reason": f"plan {winner}", "round": round}]}
    elif outcome is SelectionOutcome.REVISE:
        upd |= {"architecture_round": round + 1, "magi_mode": "revise",
                "routing": [{"from": "magi_select", "to": "magi_begin", "reason": "revise", "round": round}]}
    else:
        upd |= {"final_status": _classify_no_admissible(standings), "diagnosis": rationale + ": " + jdump([s.model_dump(mode="json") for s in standings])}
    return upd


def route_select(state: dict) -> str:
    if state.get("final_status") or state.get("selected_plan"):
        return "__end__"
    return "magi_begin"
