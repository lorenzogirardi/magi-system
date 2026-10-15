"""Subgraph C — implementation and pairing (brief §4, §9).

Strictly sequential: dev_writer → initial checks → dev_pair_assess → dev_pair → integrate.
Only one agent holds write access to the workspace at a time. Each agent's changes are
committed separately with an idempotent step key, so a resumed node reuses its commit
instead of writing again.
"""

from __future__ import annotations

from magi_system.agents.caller import untrusted
from magi_system.engine.context import RunContext, node
from magi_system.engine.gates import PROJECT_GATES, allowed_write_globs, project_gate, revert_out_of_scope
from magi_system.engine.helpers import (
    dump,
    findings_from_drafts,
    jdump,
    meta,
    policy_block,
    request_block,
    requirements_block,
    settle_findings,
    verify_claims,
)
from magi_system.engine.schemas import HandoffDraft, PairAssessmentDraft
from magi_system.models.common import AgentId, Phase
from magi_system.models.decision import AgentFailure
from magi_system.models.work import AgentHandoff, ChangeSet, FileChange, HandoffStatus, ImplementationTask


def _cycle(state: dict) -> int:
    return state.get("remediation_cycle", 0)


def _plan(state: dict) -> dict:
    return state["proposals"][state["selected_plan"]]


def _step(state: dict, agent: AgentId) -> str:
    return f"{agent.value}:p{state['plan_version']}:c{_cycle(state)}"


def _plan_block(state: dict) -> str:
    p = _plan(state)
    keep = ("title", "summary", "components", "contracts", "changes", "verification_plan", "migration_rollback",
            "documentation_impact", "post_implementation_obligations")
    return f"## Selected plan (approved, version {state['plan_version']})\n" + jdump({k: p[k] for k in keep})


def _remediation_block(state: dict, agent: AgentId) -> str:
    rem = state.get("remediation") or {}
    if rem.get("target") != agent.value:
        return ""
    items = [state["findings"][f] for f in rem.get("finding_ids", []) if f in state["findings"]]
    return "## Findings you must fix\n" + untrusted([{"id": f["meta"]["id"], **{k: f[k] for k in ("title", "detail", "severity", "category")}} for f in items])


def _diff_block(ctx: RunContext, base: str, head: str) -> str:
    diff = ctx.workspaces.diff(ctx.run_id, base, head) if base != head else ""
    return "## Diff\n" + untrusted(diff[-60000:] or "(no changes)")


def _changeset(ctx: RunContext, state: dict, agent: AgentId, base: str, head: str, phase: Phase) -> ChangeSet:
    files = tuple(
        FileChange(path=p, action={"A": "create", "M": "modify", "D": "delete"}.get(s, "modify"))
        for s, p in ctx.workspaces.changed_files(ctx.run_id, base, head)
    )
    patch = ctx.workspaces.export_patch(ctx.run_id, base, head, f"{_step(state, agent).replace(':', '_')}.patch") if files else None
    return ChangeSet(
        meta=meta("cs", agent, _cycle(state), phase, base, head, plan_version=state["plan_version"], input_hash=base),
        author=agent, base_commit=base, commit=head if files else "", files=files,
        patch_path=str(patch.relative_to(ctx.workspaces.run_dir(ctx.run_id))) if patch else "", empty=not files,
    )


def _work(state: dict, ctx: RunContext, agent: AgentId, prompt: str, phase: Phase, extra: list[str]) -> dict:
    """Shared writer/pair work: restore base, run tool loop, revert out-of-scope writes, commit."""
    step = _step(state, agent)
    base = state["candidate_commit"]
    done = ctx.workspaces.find_step_commit(ctx.run_id, step)
    if done:
        ctx.events.emit("step_reused", step=step, commit=done)
        # The handoff of a completed step is already in state when the node completed; if
        # the node crashed after committing, the commit is reused and the agent is not re-run.
        prior = next((h for h in (state.get("task_results") or {}).values() if h.get("step") == step), None)
        if prior:
            return {"candidate_commit": done}
    ctx.workspaces.restore(ctx.run_id, base)
    globs = allowed_write_globs(ctx, _plan(state))
    task = ImplementationTask(
        meta=meta("task", AgentId.PLATFORM, _cycle(state), phase, agent.value, plan_version=state["plan_version"], input_hash=base),
        title=f"{agent.value} cycle {_cycle(state)}", description=_plan(state)["summary"], allowed_paths=globs,
        finding_id=",".join((state.get("remediation") or {}).get("finding_ids", [])), owner=agent,
    )
    if done:
        head, result = done, None
    else:
        result = ctx.caller.run_tools(
            agent, purpose=prompt, round=_cycle(state), prompt_name=prompt, prompt_values={},
            body="\n\n".join([request_block(state), policy_block(ctx), requirements_block(state), _plan_block(state), *extra]),
            final_schema=HandoffDraft, toolbox=ctx.toolbox(agent, task_scopes=globs), phase=phase.value,
        )
        reverted = revert_out_of_scope(ctx, base, globs)
        if isinstance(result, AgentFailure):
            ctx.workspaces.restore(ctx.run_id, base)
            head = base
        else:
            head = ctx.workspaces.commit(ctx.run_id, f"magi: {agent.value} changes", agent.value, step)
            if head == base:
                ctx.workspaces.record_step(ctx.run_id, step, head)
        if reverted:
            ctx.events.emit("scope_enforced", agent=agent.value, reverted=reverted)
    cs = _changeset(ctx, state, agent, base, head, phase)
    if isinstance(result, AgentFailure):
        status, summary, escalations = HandoffStatus.FAILED, f"{result.kind.value}: {result.detail}", []
    elif result is None:
        status, summary, escalations = (HandoffStatus.COMPLETED if not cs.empty else HandoffStatus.NO_CHANGES), "reused committed step", []
    else:
        escalations = findings_from_drafts(result.escalations, producer=agent, round=_cycle(state), phase=phase,
                                           subject_id=state["selected_plan"], plan_version=state["plan_version"], salt=step)
        # Escalations are objections like any other: verified with tools before routing.
        verified, _ = verify_claims(ctx, [c for f in escalations for c in f.claims], commit=head, round=_cycle(state), phase=phase)
        escalations = settle_findings(escalations, {cid: c.status for cid, c in verified.items()}, ctx.events)
        status = HandoffStatus.ESCALATED if escalations else (HandoffStatus.NO_CHANGES if cs.empty else HandoffStatus.COMPLETED)
        summary = result.summary
    handoff = AgentHandoff(
        meta=meta("handoff", agent, _cycle(state), phase, step, plan_version=state["plan_version"], input_hash=base, evidence_refs=[cs.id]),
        author=agent, status=status, summary=summary, changeset_id=cs.id,
        checks_run=tuple(result.checks_run) if result else (), deviations=tuple(result.deviations) if result else (),
        escalations=tuple(f.id for f in escalations),
    )
    ctx.events.emit("handoff", agent=agent.value, step=step, status=status.value, base=base, head=head,
                    files=[f.path for f in cs.files], escalations=len(escalations))
    upd = {
        "candidate_commit": head,
        "task_results": {
            task.id: {"kind": "task", **task.model_dump(mode="json")},
            cs.id: {"kind": "changeset", **cs.model_dump(mode="json")},
            handoff.id: {"kind": "handoff", "step": step, **handoff.model_dump(mode="json")},
        },
        "findings": dump(escalations),
        "routing": [{"from": agent.value, "to": "next", "step": step, "commit": head}],
    }
    if not cs.empty:
        upd["last_code_author"] = agent.value
    if status is HandoffStatus.FAILED:
        upd["remediation"] = {"target": "technical", "agent": agent.value, "reason": summary,
                              "resume": state.get("remediation") or {}}
    return upd


@node("dev_writer")
def dev_writer(state: dict, ctx: RunContext) -> dict:
    return _work(state, ctx, AgentId.DEV1, "dev_writer", Phase.IMPLEMENTATION, [_remediation_block(state, AgentId.DEV1)])


@node("dev_initial_checks")
def dev_initial_checks(state: dict, ctx: RunContext) -> dict:
    """Quick checks on DEV1's candidate before the pair starts (informational: gates rerun later)."""
    commit = state["candidate_commit"]
    ctx.workspaces.restore(ctx.run_id, commit)
    results = [project_gate(ctx, g, commit, round=_cycle(state), phase=Phase.IMPLEMENTATION, salt="dev1-initial")
               for g in PROJECT_GATES]
    return {"task_results": {r.id: {"kind": "initial_check", **r.model_dump(mode="json")} for r in results},
            "dev1_initial_checks": {r.subject_id: r.verdict.value for r in results}}


@node("dev_pair_assess")
def dev_pair_assess(state: dict, ctx: RunContext) -> dict:
    """DEV2 step 1: independent assessment from request, plan and diff — WITHOUT DEV1's handoff."""
    key = _step(state, AgentId.DEV2)
    if key in (state.get("initial_assessments") or {}):
        return {}
    base = state["baseline_commit"]
    checks = [r for r in (state.get("task_results") or {}).values()
              if r.get("kind") == "initial_check" and r["candidate_commit"] == state["candidate_commit"]]
    result = ctx.caller.ask(
        AgentId.DEV2, purpose="pair_assessment", round=_cycle(state), prompt_name="dev_pair_assessment", prompt_values={},
        body="\n\n".join([request_block(state), requirements_block(state), _plan_block(state),
                          _diff_block(ctx, base, state["candidate_commit"]),
                          "## Initial check results\n" + untrusted([{k: c[k] for k in ("check", "verdict", "detail")} for c in checks])]),
        schema=PairAssessmentDraft, phase=Phase.PAIRING.value,
    )
    if isinstance(result, AgentFailure):
        return {"initial_assessments": {key: {"ok": False, "failure": result.model_dump(mode="json")}},
                "remediation": {"target": "technical", "agent": AgentId.DEV2.value, "reason": result.detail,
                                "resume": state.get("remediation") or {}}}
    ctx.events.emit("pair_initial_assessment", step=key, concerns=len(result.concerns))
    return {"initial_assessments": {key: {"ok": True, "assessment": result.assessment, "concerns": result.concerns,
                                          "candidate": state["candidate_commit"]}}}


@node("dev_pair")
def dev_pair(state: dict, ctx: RunContext) -> dict:
    """DEV2 step 2: with the handoff, run tests, fix local issues or conclude with no changes."""
    pre = (state.get("initial_assessments") or {}).get(_step(state, AgentId.DEV2), {})
    dev1_handoffs = sorted(
        (h for h in (state.get("task_results") or {}).values() if h.get("kind") == "handoff" and h["author"] == AgentId.DEV1.value),
        key=lambda h: h["meta"]["round"],
    )
    extra = [
        _diff_block(ctx, state["baseline_commit"], state["candidate_commit"]),
        "## Your initial assessment (written before reading the handoff)\n" + untrusted(pre.get("assessment", "")),
        "## DEV1 handoff\n" + untrusted({k: dev1_handoffs[-1][k] for k in ("summary", "checks_run", "deviations")} if dev1_handoffs else {}),
        _remediation_block(state, AgentId.DEV2),
    ]
    upd = _work(state, ctx, AgentId.DEV2, "dev_pair", Phase.PAIRING, extra)
    for h in upd.get("task_results", {}).values():
        if h.get("kind") == "handoff":
            h["initial_assessment"] = pre.get("assessment", "")
    return upd


def route_after_writer(state: dict) -> str:
    if state.get("final_status"):
        return "__end__"
    if (state.get("remediation") or {}).get("target") == "technical":
        return "impl_technical"
    # Escalations do not stop the pair: triage verifies them after the subgraph.
    return "dev_initial_checks"


def route_after_pair(state: dict) -> str:
    if state.get("final_status"):
        return "__end__"
    if (state.get("remediation") or {}).get("target") == "technical":
        return "impl_technical"
    return "__end__"


def _escalated(state: dict, agent: AgentId) -> bool:
    step = _step(state, agent)
    return any(h.get("kind") == "handoff" and h.get("step") == step and h["status"] == HandoffStatus.ESCALATED.value
               for h in (state.get("task_results") or {}).values())


@node("impl_technical")
def impl_technical(state: dict, ctx: RunContext) -> dict:
    """Technical retry of a failed agent step, separate from correctness fix loops."""
    rem = state.get("remediation") or {}
    agent = rem.get("agent", "")
    key = f"impl:{_step(state, AgentId(agent))}" if agent else "impl:unknown"
    used = (state.get("technical_retries") or {}).get(key, 0)
    if used >= ctx.run_input.budget.max_technical_retries:
        return {"final_status": "INFRA_FAILURE", "diagnosis": f"{agent} failed after {used} technical retries: {rem.get('reason', '')}"}
    ctx.events.emit("technical_retry", stage="implementation", agent=agent, attempt=used + 1)
    initial = dict(state.get("initial_assessments") or {})
    step = _step(state, AgentId.DEV2)
    reset = {step: None} if agent == AgentId.DEV2.value and not (initial.get(step) or {}).get("ok", True) else {}
    return {"technical_retries": {key: used + 1}, "remediation": rem.get("resume") or {}, "initial_assessments": reset,
            "routing": [{"from": "impl_technical", "to": agent, "attempt": used + 1}]}


def route_technical(state: dict) -> str:
    if state.get("final_status"):
        return "__end__"
    return "dev_writer" if (state.get("routing") or [{}])[-1].get("to") == AgentId.DEV1.value else "dev_pair_assess"


def route_after_assess(state: dict) -> str:
    if (state.get("remediation") or {}).get("target") == "technical":
        return "impl_technical"
    return "dev_pair"


def route_entry(state: dict) -> str:
    """Entry of subgraph C: start with the writer unless the pending fix belongs to the pair."""
    return "dev_pair_assess" if (state.get("remediation") or {}).get("target") == AgentId.DEV2.value else "dev_writer"
