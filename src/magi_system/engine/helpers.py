"""Helpers shared by graph nodes: artifact construction from drafts, claim verification,
round-scoped fan-out/fan-in bookkeeping."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from langgraph.types import Send

from magi_system.agents.caller import untrusted
from magi_system.decision.selection import resolve_finding_status
from magi_system.decision.verifier import ClaimVerifier
from magi_system.engine.context import RunContext
from magi_system.engine.schemas import AssumptionDraft, ClaimDraft, FindingDraft
from magi_system.models.common import AgentId, ArtifactMeta, Phase, content_hash, stable_id
from magi_system.models.decision import AgentFailure, Finding, FindingCategory, Severity
from magi_system.models.knowledge import Assumption, Claim, ClaimStatus

FAILURE_RETRYABLE = {"timeout", "invalid_output", "provider_error"}


def meta(
    prefix: str,
    producer: AgentId,
    round: int,
    phase: Phase,
    *parts: Any,
    plan_version: int = 0,
    input_hash: str = "",
    evidence_refs: Iterable[str] = (),
) -> ArtifactMeta:
    return ArtifactMeta(
        id=stable_id(prefix, producer.value, round, phase.value, plan_version, *parts),
        producer=producer,
        round=round,
        phase=phase,
        plan_version=plan_version,
        input_hash=input_hash or content_hash(list(parts)),
        evidence_refs=tuple(evidence_refs),
    )


def claims_from_drafts(drafts: Iterable[ClaimDraft], owner: str) -> list[Claim]:
    return [
        Claim(
            id=stable_id("clm", owner, i, d.kind.value, d.target, d.pattern, d.expect),
            kind=d.kind, statement=d.statement, target=d.target, pattern=d.pattern,
            expect=d.expect, critical=d.critical,
        )
        for i, d in enumerate(drafts)
    ]


def assumptions_from_drafts(drafts: Iterable[AssumptionDraft], claims: list[Claim], owner: str) -> list[Assumption]:
    out = []
    for i, d in enumerate(drafts):
        deps = tuple(claims[j].id for j in d.supporting_claims if 0 <= j < len(claims))
        out.append(Assumption(id=stable_id("asm", owner, i, d.text), text=d.text, critical=d.critical, depends_on_claims=deps))
    return out


def resolve_assumptions(assumptions: list[Assumption], claim_status: dict[str, ClaimStatus]) -> list[Assumption]:
    """An assumption is resolved only when every supporting claim is SUPPORTED by tools.
    A refuted supporting claim invalidates it (and therefore any decision depending on it)."""
    out = []
    for a in assumptions:
        statuses = [claim_status.get(c) for c in a.depends_on_claims]
        if a.depends_on_claims and all(s is ClaimStatus.SUPPORTED for s in statuses):
            out.append(a.model_copy(update={"resolved": True, "resolution": "supported by verified claims"}))
        elif any(s is ClaimStatus.REFUTED for s in statuses):
            out.append(a.model_copy(update={"resolved": False, "resolution": "supporting claim refuted"}))
        else:
            out.append(a.model_copy(update={"resolved": False, "resolution": "no verified support"}))
    return out


def verify_claims(ctx: RunContext, claims: Iterable[Claim], *, commit: str, round: int, phase: Phase = Phase.CLAIM_VERIFICATION):
    """Verify claims with tools on the current workspace (which must be at `commit`)."""
    verifier = ClaimVerifier(ctx.workspace, ctx.policy, ctx.runner, commit, round, phase)
    verified, results = {}, {}
    for claim in claims:
        if claim.id in verified:
            continue
        ctx.budget.on_tool_call("verify_claim")
        new_claim, result = verifier.verify(claim)
        verified[new_claim.id] = new_claim
        results[result.meta.id] = result
        ctx.events.emit(
            "claim_verified", claim=claim.id, kind=claim.kind.value, target=claim.target,
            status=new_claim.status.value, detail=result.detail, commit=commit,
        )
    return verified, results


def findings_from_drafts(
    drafts: Iterable[FindingDraft], *, producer: AgentId, round: int, phase: Phase, subject_id: str, plan_version: int = 0, salt: str = ""
) -> list[Finding]:
    out = []
    for i, d in enumerate(drafts):
        owner = f"{producer.value}:{phase.value}:{round}:{subject_id}:{salt}:{i}"
        out.append(
            Finding(
                meta=meta("fnd", producer, round, phase, subject_id, salt, i, d.title, plan_version=plan_version),
                subject_id=subject_id, category=FindingCategory(d.category), severity=Severity(d.severity),
                title=d.title, detail=d.detail, claims=tuple(claims_from_drafts(d.claims, owner)),
            )
        )
    return out


def settle_findings(findings: list[Finding], claim_status: dict[str, ClaimStatus], events) -> list[Finding]:
    out = []
    for f in findings:
        status, reason = resolve_finding_status(f, claim_status)
        claims = tuple(c.model_copy(update={"status": claim_status.get(c.id, c.status)}) for c in f.claims)
        nf = f.model_copy(update={"status": status, "status_reason": reason, "claims": claims})
        events.emit("finding", id=nf.id, producer=nf.meta.producer.value, subject=nf.subject_id, severity=nf.severity.value,
                    category=nf.category.value, status=nf.status.value, title=nf.title, reason=reason)
        out.append(nf)
    return out


def claim_status_map(state: dict) -> dict[str, ClaimStatus]:
    return {cid: ClaimStatus(c["status"]) for cid, c in (state.get("claims") or {}).items()}


def dump(models: Iterable) -> dict[str, dict]:
    return {m.id: m.model_dump(mode="json") for m in models}


def jdump(value: Any) -> str:
    return json.dumps(value, indent=1, default=str, sort_keys=True)


# -- fan-out / fan-in -----------------------------------------------------------------------


def stage_key(stage: str, scope: str, agent: AgentId) -> str:
    return f"{stage}:{scope}:{agent.value}"


def pending_agents(state: dict, stage: str, scope: str, agents: Iterable[AgentId]) -> list[AgentId]:
    """Agents whose result for (stage, scope) is missing or marked for a technical retry."""
    results = state.get("stage_results") or {}
    out = []
    for a in agents:
        r = results.get(stage_key(stage, scope, a))
        if r is None or r.get("retry"):
            out.append(a)
    return out


def sends(state: dict, node_name: str, stage: str, scope: str, agents: Iterable[AgentId]) -> list[Send]:
    payload = {k: v for k, v in state.items() if k != "fanout"}
    return [Send(node_name, {**payload, "fanout": {"agent": a.value, "key": stage_key(stage, scope, a), "scope": scope}}) for a in agents]


def fan_in(state: dict, stage: str, scope: str, agents: Iterable[AgentId]) -> tuple[dict[AgentId, dict], dict[AgentId, dict], list[AgentId]]:
    """Split expected results into ok / failed / missing (missing must be empty at fan-in)."""
    results = state.get("stage_results") or {}
    ok, failed, missing = {}, {}, []
    for a in agents:
        r = results.get(stage_key(stage, scope, a))
        if r is None:
            missing.append(a)
        elif r.get("ok"):
            ok[a] = r
        else:
            failed[a] = r
    return ok, failed, missing


def failure_result(f: AgentFailure) -> dict:
    return {"ok": False, "failure": {"kind": f.kind.value, "detail": f.detail, "agent": f.agent.value}}


def collect_stage(
    state: dict, ctx, stage: str, scope: str, agents: Iterable[AgentId], *, quorum: int | None = None
) -> tuple[str, dict, dict[AgentId, dict], dict[AgentId, dict]]:
    """Fan-in for one stage. Returns (decision, update, ok, failed):

    - "retry": some agents failed retryably and have retries left; `update` marks them;
    - "terminal": budget/infra failure, or fewer valid results than the quorum;
    - "ok": proceed with `ok` results (and typed `failed` ones, if the quorum allows).

    All expected agents must have reported (typed failures count as reports); results of
    other rounds/stages are never read because keys are scoped by stage and round.
    """
    agents = list(agents)
    ok, failed, missing = fan_in(state, stage, scope, agents)
    if missing:
        raise RuntimeError(f"fan-in for {stage}:{scope} reached with missing results: {[a.value for a in missing]}")
    for r in failed.values():
        if r.get("budget"):
            return "terminal", {"final_status": "BUDGET_EXHAUSTED", "diagnosis": f"{stage}: {r.get('detail')}"}, ok, failed
    for r in failed.values():
        if r.get("infra"):
            return "terminal", {"final_status": "INFRA_FAILURE", "diagnosis": f"{stage}: {r.get('detail')}"}, ok, failed
    retries = state.get("technical_retries") or {}
    max_retries = ctx.run_input.budget.max_technical_retries
    counters, marks = {}, {}
    for a, r in failed.items():
        key = stage_key(stage, scope, a)
        if r.get("failure", {}).get("kind") in FAILURE_RETRYABLE and retries.get(key, 0) < max_retries:
            counters[key] = retries.get(key, 0) + 1
            marks[key] = {**r, "retry": True}
    if counters:
        ctx.events.emit("technical_retry", stage=stage, scope=scope, agents=sorted(counters))
        return "retry", {"technical_retries": counters, "stage_results": marks}, ok, failed
    needed = len(agents) if quorum is None else quorum
    if len(ok) < needed:
        detail = "; ".join(f"{a.value}: {r['failure']['kind']} ({r['failure']['detail'][:200]})" for a, r in failed.items() if "failure" in r)
        return "terminal", {
            "final_status": "INFRA_FAILURE",
            "diagnosis": f"{stage}: only {len(ok)} of {len(agents)} expected results are valid (need {needed}); {detail}",
        }, ok, failed
    return "ok", {}, ok, failed


# -- prompt body rendering (repository-derived content is always wrapped as untrusted) ----------


def request_block(state: dict) -> str:
    return "## Change request (the mandate)\n" + untrusted(state["original_request"]["text"])


def policy_block(ctx) -> str:
    p = ctx.policy
    return "## Platform policy (binding)\n" + jdump(
        {
            "policy_version": p.version,
            "mandatory_gates": list(p.mandatory_gates),
            "protected_paths": list(p.protected_paths),
            "allowed_commands": p.effective["command_allowlist"],
            "publication": p.effective["publication"]["allowed_modes"],
        }
    )


def repo_block(state: dict) -> str:
    snap = state["repository_snapshot"]
    summary = {
        "target": snap["target_identity"],
        "commit": snap["commit"],
        "languages": snap["languages"],
        "manifests": snap["manifests"],
        "files": [f["path"] for f in snap["files"]][:500],
        "project_config": state.get("project_config", {}),
    }
    return "## Repository snapshot\n" + untrusted(summary)


def requirements_block(state: dict) -> str:
    reqs = [
        {"id": r["meta"]["id"], "kind": r["kind"], "text": r["text"], "mandatory": r["mandatory"]}
        for r in (state.get("requirements") or {}).values()
    ]
    return "## Requirements (use these ids for coverage)\n" + jdump(sorted(reqs, key=lambda r: r["id"]))


def masked_proposal(p: dict) -> dict:
    hidden = {"meta", "author", "derived_from", "version"}
    return {k: v for k, v in p.items() if k not in hidden}
