"""Subgraphs D (specialist verification), E (documentation), F (final MAGI review) and the
deterministic triage that routes between them (brief §9, §10).

Every result is keyed by candidate commit. A new candidate makes previous results stale:
they are never read for a different commit (no stale evidence reuse).
"""

from __future__ import annotations

from magi_system.agents.caller import untrusted
from magi_system.agents.profiles import PROFILES
from magi_system.engine.context import RunContext, node
from magi_system.engine.gates import (
    PROJECT_GATES,
    gate_result,
    integrity_check,
    project_gate,
    revert_out_of_scope,
    run_command_check,
    scope_check,
)
from magi_system.engine.helpers import (
    collect_stage,
    dump,
    failure_result,
    findings_from_drafts,
    jdump,
    meta,
    pending_agents,
    policy_block,
    request_block,
    requirements_block,
    sends,
    settle_findings,
    verify_claims,
)
from magi_system.engine.schemas import CandidateReviewDraft, DocsDraft
from magi_system.models.common import MAGI_IDS, AgentId, Phase, Verdict, stable_id
from magi_system.models.decision import AgentFailure, Finding, FindingCategory, FindingStatus, Severity
from magi_system.models.work import DocKind, DocumentationResult, DocUpdate

SPECIALISTS = (AgentId.QA, AgentId.SRE, AgentId.SECURITY)


def _commit(state: dict) -> str:
    return state["candidate_commit"]


def _short(c: str) -> str:
    return c[:12]


def _plan_summary(state: dict) -> str:
    p = state["proposals"][state["selected_plan"]]
    return "## Approved plan\n" + jdump({k: p[k] for k in ("title", "summary", "changes", "verification_plan", "post_implementation_obligations")})


def _checks_for(state: dict, commit: str) -> list[dict]:
    return [r for r in (state.get("gate_results") or {}).values() if r["candidate_commit"] == commit]


# -- D: mechanical checks + specialist reviews ------------------------------------------------


@node("verify_checks")
def verify_checks(state: dict, ctx: RunContext) -> dict:
    """Run project gates, QA acceptance commands, integrity and scope checks on the candidate."""
    commit = _commit(state)
    ctx.workspaces.restore(ctx.run_id, commit)
    rnd = state.get("remediation_cycle", 0)
    results = [project_gate(ctx, g, commit, round=rnd, phase=Phase.SPECIALIST_VERIFICATION) for g in PROJECT_GATES]
    scenarios = list((state.get("acceptance_scenarios") or {}).values())
    latest = max((s["round"] for s in scenarios), default=0)
    for sc in sorted((s for s in scenarios if s["round"] == latest), key=lambda s: s["id"]):
        if not sc["command"] or sc["denied"] or sc.get("superseded"):
            continue
        results.append(run_command_check(ctx, f"qa.scenario:{sc['name']}", sc["command"], commit, round=rnd,
                                          phase=Phase.SPECIALIST_VERIFICATION, scenario=True))
    results.append(integrity_check(ctx, state, commit, round=rnd, phase=Phase.SPECIALIST_VERIFICATION))
    results.append(scope_check(ctx, state, commit, round=rnd, phase=Phase.SPECIALIST_VERIFICATION))
    # Executing commands must not alter the candidate.
    revert_out_of_scope(ctx, commit, ())
    return {"gate_results": {f"{r.subject_id}@{commit}": r.model_dump(mode="json") for r in results}}


def acceptance_verdict(scenario_checks: dict[str, str], blocking_titles: list[str]) -> tuple[Verdict, str]:
    """QA acceptance: at least one scenario PASSes, none FAILs. NOT_APPLICABLE scenarios (advisory, see
    gates.scenario_verdict) are reported but never decide."""
    passed = [k for k, v in scenario_checks.items() if v == Verdict.PASS.value]
    advisory = [k for k, v in scenario_checks.items() if v == Verdict.NOT_APPLICABLE.value]
    note = f"; advisory (not counted): {advisory}" if advisory else ""
    if not passed:
        return Verdict.UNKNOWN, f"no acceptance scenario demonstrated the change{note}"
    failed = {k: v for k, v in scenario_checks.items() if v not in (Verdict.PASS.value, Verdict.NOT_APPLICABLE.value)}
    if failed:
        return Verdict.FAIL, f"acceptance scenarios not passing: {failed}{note}"
    if blocking_titles:
        return Verdict.FAIL, f"confirmed QA findings: {blocking_titles}"
    return Verdict.PASS, f"{len(passed)} acceptance scenario(s) passed; QA review without confirmed blockers{note}"


def pick_scenario_rewrites(scenarios: dict, executed: dict[str, str], rewrites, command_allowed) -> list[tuple[dict, object]]:
    """Which QA rewrites are accepted: a scenario that FAILED when executed, at most once per scenario, with a
    different command the command policy allows. The corrected command is then EXECUTED: the verdict stays
    that of an execution, never the QA's opinion."""
    live = [s for s in scenarios.values() if not s.get("superseded")]
    already = {s["name"] for s in scenarios.values() if s.get("amended_from")}
    picked = []
    for new in rewrites:
        old = next((s for s in live if s["name"] == new.id and not s.get("amended_from")), None)
        if (old is None or new.id in already or not new.command or new.command == old["command"]
                or executed.get(f"qa.scenario:{new.id}") != Verdict.FAIL.value or not command_allowed(new.command)):
            continue
        picked.append((old, new))
    return picked


def route_to_specialists(state: dict):
    scope = _short(_commit(state))
    return sends(state, "specialist_review", "specialist", scope, pending_agents(state, "specialist", scope, SPECIALISTS))


@node("specialist_review")
def specialist_review(state: dict, ctx: RunContext) -> dict:
    agent, key = AgentId(state["fanout"]["agent"]), state["fanout"]["key"]
    profile = PROFILES[agent]
    commit = _commit(state)
    checks = [{k: r[k] for k in ("check", "verdict", "command", "detail")} for r in _checks_for(state, commit)]
    diff = ctx.workspaces.diff(ctx.run_id, state["baseline_commit"], commit)
    scenarios = [{k: s[k] for k in ("name", "description", "command", "denied")} for s in (state.get("acceptance_scenarios") or {}).values()]
    result = ctx.caller.ask(
        agent, purpose="candidate_review", round=state.get("remediation_cycle", 0), prompt_name="specialist_review",
        prompt_values={"display_name": profile.display_name, "perspective": profile.perspective},
        body="\n\n".join([request_block(state), policy_block(ctx), requirements_block(state), _plan_summary(state),
                          "## Diff\n" + untrusted(diff[-60000:]), "## Executed checks\n" + untrusted(checks),
                          "## Acceptance scenarios\n" + untrusted(scenarios)]),
        schema=CandidateReviewDraft, phase=Phase.SPECIALIST_VERIFICATION.value,
    )
    if isinstance(result, AgentFailure):
        return {"stage_results": {key: failure_result(result)}}
    return {"stage_results": {key: {"ok": True, "draft": result.model_dump(mode="json"), "commit": commit}}}


def _review_gate(agent: AgentId) -> str:
    return {AgentId.QA: "qa.acceptance", AgentId.SRE: "sre.review", AgentId.SECURITY: "security.review"}[agent]


@node("specialist_collect")
def specialist_collect(state: dict, ctx: RunContext) -> dict:
    commit, scope = _commit(state), _short(_commit(state))
    decision, update, ok, failed = collect_stage(state, ctx, "specialist", scope, SPECIALISTS)
    if decision == "retry":
        return update
    if decision == "terminal" and update.get("final_status") == "BUDGET_EXHAUSTED":
        return update
    rnd = state.get("remediation_cycle", 0)
    findings: list[Finding] = []
    gates = {}
    for agent in SPECIALISTS:
        gate = _review_gate(agent)
        if agent not in ok:
            gates[gate] = gate_result(gate, commit, Verdict.ERROR, f"{agent.value} review failed: {failed[agent].get('failure', {}).get('detail', '')}",
                                      round=rnd, phase=Phase.SPECIALIST_VERIFICATION, producer=agent)
            continue
        draft = CandidateReviewDraft.model_validate(ok[agent]["draft"])
        fs = findings_from_drafts(draft.findings, producer=agent, round=rnd, phase=Phase.SPECIALIST_VERIFICATION,
                                  subject_id=commit, plan_version=state["plan_version"])
        findings += fs
        gates[gate] = {"agent": agent, "draft": draft, "findings": fs}
    # Verify specialist claims on the candidate with tools.
    claims = [c for f in findings for c in f.claims]
    verified, results = verify_claims(ctx, claims, commit=commit, round=rnd, phase=Phase.SPECIALIST_VERIFICATION)
    status = {cid: c.status for cid, c in verified.items()}
    settled = {f.id: f for f in settle_findings(findings, status, ctx.events)}
    out_gates = {}
    executed = {r["check"]: r["verdict"] for r in _checks_for(state, commit)}
    amended_checks: dict = {}
    scenario_updates: dict = {}
    qa = gates.get(_review_gate(AgentId.QA))
    if isinstance(qa, dict):  # the QA saw the executed checks: it may correct a scenario that is wrong, not the code
        for old, new in pick_scenario_rewrites(state.get("acceptance_scenarios") or {}, executed, qa["draft"].acceptance_scenarios,
                                               lambda c: ctx.policy.check_command(c).allowed):
            sid = stable_id("acc", old["round"], new.id, new.command)
            scenario_updates[old["id"]] = {**old, "superseded": True}
            scenario_updates[sid] = {**old, "id": sid, "description": new.description, "command": new.command, "denied": False,
                                     "denial_reason": "", "amended_from": old["id"], "superseded": False}
            ctx.events.emit("scenario_amended", scenario=new.id, old_command=old["command"], new_command=new.command, reason=new.description[:500])
            result = run_command_check(ctx, f"qa.scenario:{new.id}", new.command, commit, round=rnd,
                                       phase=Phase.SPECIALIST_VERIFICATION, salt="amended", scenario=True)
            executed[result.subject_id] = result.verdict.value
            amended_checks[f"{result.subject_id}@{commit}"] = result.model_dump(mode="json")
    for gate, g in gates.items():
        if not isinstance(g, dict):
            out_gates[gate] = g
            continue
        agent, draft = g["agent"], g["draft"]
        mine = [settled[f.id] for f in g["findings"]]
        blocking = [f for f in mine if f.status is FindingStatus.CONFIRMED and f.severity in (Severity.MAJOR, Severity.BLOCKER)]
        if agent is AgentId.QA:
            scenario_checks = {k: v for k, v in executed.items() if k.startswith("qa.scenario:")}
            verdict, detail = acceptance_verdict(scenario_checks, [f.title for f in blocking])
            if scenario_updates:
                detail += f"; scenarios corrected by QA and re-executed: {sorted(s['name'] for s in scenario_updates.values() if s.get('amended_from'))}"
        elif agent is AgentId.SRE and draft.not_applicable_reason and not mine:
            if ctx.policy.not_applicable_allowed(gate):
                verdict, detail = Verdict.NOT_APPLICABLE, f"not applicable: {draft.not_applicable_reason}"
            else:
                verdict, detail = Verdict.UNKNOWN, "SRE review claimed not applicable but policy requires it"
        else:
            verdict = Verdict.FAIL if blocking else Verdict.PASS
            detail = f"confirmed findings: {[f.title for f in blocking]}" if blocking else f"review completed; {len(mine)} finding(s), none confirmed blocking"
        out_gates[gate] = gate_result(gate, commit, verdict, detail, round=rnd, phase=Phase.SPECIALIST_VERIFICATION, producer=agent)
        ctx.events.emit("check", gate=gate, commit=commit, verdict=verdict.value, detail=detail[:300])
    return {
        "findings": {fid: f.model_dump(mode="json") for fid, f in settled.items()},
        "claims": {cid: c.model_dump(mode="json") for cid, c in verified.items()},
        "verification_results": dump(results.values()),
        "gate_results": {**amended_checks, **{f"{gate}@{commit}": r.model_dump(mode="json") for gate, r in out_gates.items()}},
        "acceptance_scenarios": scenario_updates,
        "specialist_results": {f"{a.value}@{commit}": {"agent": a.value, "commit": commit, "ok": a in ok} for a in SPECIALISTS},
    }


def route_specialists(state: dict):
    if state.get("final_status"):
        return "__end__"
    scope = _short(_commit(state))
    retry = [a for a in SPECIALISTS if (state.get("stage_results") or {}).get(f"specialist:{scope}:{a.value}", {}).get("retry")]
    if retry:
        return sends(state, "specialist_review", "specialist", scope, retry)
    return "__end__"


# -- E: documentation -------------------------------------------------------------------------


@node("tech_writer")
def tech_writer(state: dict, ctx: RunContext) -> dict:
    base = _commit(state)
    step = f"{AgentId.TECH_WRITER.value}:p{state['plan_version']}:d{state.get('docs_cycle', 0)}"
    done = ctx.workspaces.find_step_commit(ctx.run_id, step)
    if done and any(d.get("step") == step for d in (state.get("documentation_results") or {}).values()):
        return {"candidate_commit": done}
    ctx.workspaces.restore(ctx.run_id, base)
    impacts = [d for k, d in (state.get("documentation_results") or {}).items() if k.startswith("impact:")]
    doc_findings = [f for f in (state.get("findings") or {}).values() if f["category"] == FindingCategory.DOCUMENTATION.value
                    and f["status"] == FindingStatus.CONFIRMED.value]
    diff = ctx.workspaces.diff(ctx.run_id, state["baseline_commit"], base)
    result = ctx.caller.run_tools(
        AgentId.TECH_WRITER, purpose="documentation", round=state.get("docs_cycle", 0), prompt_name="tech_writer", prompt_values={},
        body="\n\n".join([request_block(state), _plan_summary(state), "## Implemented diff (what exists)\n" + untrusted(diff[-60000:]),
                          "## Documentation impact assessed before coding\n" + untrusted(impacts),
                          "## Documentation scopes you may write\n" + jdump(list(ctx.doc_scopes)),
                          "## Documentation findings to address\n" + untrusted([{k: f[k] for k in ("title", "detail")} for f in doc_findings])]),
        final_schema=DocsDraft, toolbox=ctx.toolbox(AgentId.TECH_WRITER, with_runner=False), phase=Phase.DOCUMENTATION.value,
    )
    revert_out_of_scope(ctx, base, ctx.doc_scopes, docs_only=True)
    if isinstance(result, AgentFailure):
        ctx.workspaces.restore(ctx.run_id, base)
        return {"documentation_results": {f"docs@{base}": {"ok": False, "step": step, "candidate_commit": base, "detail": result.detail}}}
    head = ctx.workspaces.commit(ctx.run_id, "magi: documentation update", AgentId.TECH_WRITER.value, step)
    if head == base:
        ctx.workspaces.record_step(ctx.run_id, step, head)
    changed = [p for _, p in ctx.workspaces.changed_files(ctx.run_id, base, head)]
    updates = []
    for u in result.updates:
        try:
            kind = DocKind(u.get("kind", "reference"))
        except ValueError:
            kind = DocKind.REFERENCE
        if u.get("path") in changed:
            updates.append(DocUpdate(path=u["path"], kind=kind, summary=u.get("summary", "")))
    doc = DocumentationResult(
        meta=meta("doc", AgentId.TECH_WRITER, state.get("docs_cycle", 0), Phase.DOCUMENTATION, base, head, plan_version=state["plan_version"], input_hash=base),
        candidate_commit=head, impact=tuple(i for d in impacts for i in d.get("impact", [])), updates=tuple(updates),
        planned_only_mentions=tuple(result.not_documented_as_implemented),
    )
    ctx.events.emit("documentation", step=step, base=base, head=head, files=changed, no_update_reason=result.no_update_reason)
    return {
        "candidate_commit": head,
        "documentation_results": {f"docs@{head}": {"ok": True, "step": step, "changed": changed, "no_update_reason": result.no_update_reason,
                                                  "doc_base": base, **doc.model_dump(mode="json")}},
        "routing": [{"from": "tech_writer", "to": "triage", "commit": head}],
    }


# -- F: final MAGI review ---------------------------------------------------------------------


def route_to_final_review(state: dict):
    scope = _short(_commit(state))
    return sends(state, "final_review", "final", scope, pending_agents(state, "final", scope, MAGI_IDS))


@node("final_review")
def final_review(state: dict, ctx: RunContext) -> dict:
    """Fresh context: request, requirements, plan, final diff, evidence, docs. No design transcript."""
    agent, key = AgentId(state["fanout"]["agent"]), state["fanout"]["key"]
    profile = PROFILES[agent]
    commit = _commit(state)
    evidence = [{k: r[k] for k in ("check", "verdict", "detail")} for r in _checks_for(state, commit)]
    docs = [d for k, d in (state.get("documentation_results") or {}).items() if k.startswith("docs@")]
    result = ctx.caller.ask(
        agent, purpose="final_review", round=state.get("remediation_cycle", 0), prompt_name="magi_final_review",
        prompt_values={"display_name": profile.display_name, "focus": profile.final_review_focus},
        body="\n\n".join([request_block(state), policy_block(ctx), requirements_block(state), _plan_summary(state),
                          "## Final diff\n" + untrusted(ctx.workspaces.diff(ctx.run_id, state["baseline_commit"], commit)[-60000:]),
                          "## Verification evidence\n" + untrusted(evidence), "## Documentation\n" + untrusted(docs)]),
        schema=CandidateReviewDraft, phase=Phase.FINAL_REVIEW.value,
    )
    if isinstance(result, AgentFailure):
        return {"stage_results": {key: failure_result(result)}}
    return {"stage_results": {key: {"ok": True, "draft": result.model_dump(mode="json"), "commit": commit}}}


@node("final_collect")
def final_collect(state: dict, ctx: RunContext) -> dict:
    commit, scope = _commit(state), _short(_commit(state))
    decision, update, ok, failed = collect_stage(state, ctx, "final", scope, MAGI_IDS)
    if decision == "retry":
        return update
    if decision == "terminal" and update.get("final_status") == "BUDGET_EXHAUSTED":
        return update
    rnd = state.get("remediation_cycle", 0)
    findings = []
    for agent, r in ok.items():
        draft = CandidateReviewDraft.model_validate(r["draft"])
        findings += findings_from_drafts(draft.findings, producer=agent, round=rnd, phase=Phase.FINAL_REVIEW, subject_id=commit,
                                         plan_version=state["plan_version"])
    verified, results = verify_claims(ctx, [c for f in findings for c in f.claims], commit=commit, round=rnd, phase=Phase.FINAL_REVIEW)
    settled = settle_findings(findings, {cid: c.status for cid, c in verified.items()}, ctx.events)
    blocking = [f for f in settled if f.status is FindingStatus.CONFIRMED and f.severity in (Severity.MAJOR, Severity.BLOCKER)]
    if failed:
        verdict, detail = Verdict.ERROR, f"final review incomplete: {sorted(a.value for a in failed)}"
    elif blocking:
        verdict, detail = Verdict.FAIL, f"confirmed final-review findings: {[f.title for f in blocking]}"
    else:
        verdict, detail = Verdict.PASS, f"three final reviews completed; {len(settled)} finding(s), none confirmed blocking"
    gr = gate_result("magi.final_review", commit, verdict, detail, round=rnd, phase=Phase.FINAL_REVIEW)
    ctx.events.emit("check", gate="magi.final_review", commit=commit, verdict=verdict.value, detail=detail[:300])
    return {"findings": dump(settled), "claims": {cid: c.model_dump(mode="json") for cid, c in verified.items()},
            "verification_results": dump(results.values()), "gate_results": {f"magi.final_review@{commit}": gr.model_dump(mode="json")}}


def route_final(state: dict):
    if state.get("final_status"):
        return "__end__"
    scope = _short(_commit(state))
    retry = [a for a in MAGI_IDS if (state.get("stage_results") or {}).get(f"final:{scope}:{a.value}", {}).get("retry")]
    if retry:
        return sends(state, "final_review", "final", scope, retry)
    return "__end__"

