"""Deterministic triage between phases (brief §9) and final gate G (brief §10, §11).

Triage is plain code. It decides, for the current candidate commit, which stage runs next:
  verify (D) → docs (E) → final review (F) → gate (G)
and routes confirmed issues:
  local bug → DEV1 (then pair and all checks); pair bug → DEV2 (then checks);
  architecture → MAGI subgraph; documentation → Tech Writer; tooling → technical retry.

Every stage result is keyed by candidate commit, so a fix (new commit) automatically
invalidates earlier evidence: stages simply have no result for the new commit yet.
"""

from __future__ import annotations

from magi_system.engine.context import RunContext, node
from magi_system.models.common import AgentId, Verdict
from magi_system.models.decision import FindingCategory, FindingStatus, Severity
from magi_system.models.run import GateCheck

FIXABLE_GATES = {"project.build", "project.test", "project.lint"}


def _commit(state: dict) -> str:
    return state["candidate_commit"]


def _gate(state: dict, name: str, commit: str) -> dict | None:
    return (state.get("gate_results") or {}).get(f"{name}@{commit}")


def _confirmed(state: dict, *, subject: str | None = None, categories: set[str] | None = None, plan: bool = False) -> list[dict]:
    out = []
    for f in (state.get("findings") or {}).values():
        if f["status"] != FindingStatus.CONFIRMED.value or f["severity"] not in (Severity.MAJOR.value, Severity.BLOCKER.value):
            continue
        if subject is not None and f["subject_id"] != subject:
            continue
        if plan and f["subject_id"] != state.get("selected_plan"):
            continue
        if categories and f["category"] not in categories:
            continue
        out.append(f)
    return out


def _escalations(state: dict) -> list[dict]:
    """Architecture escalations raised by developers against the current plan, verified."""
    return [f for f in _confirmed(state, plan=True, categories={FindingCategory.ARCHITECTURE.value})
            if f["meta"]["producer"] in (AgentId.DEV1.value, AgentId.DEV2.value)
            and f["meta"]["id"] not in set((state.get("remediation") or {}).get("handled", []))]


@node("triage")
def triage(state: dict, ctx: RunContext) -> dict:
    commit = _commit(state)
    budget = ctx.run_input.budget
    rem_cycle = state.get("remediation_cycle", 0)
    def fix(target: str, finding_ids: list[str], reason: str, counter_key: str) -> dict:
        counters = state.get("remediation_counters") or {}
        if rem_cycle + 1 > budget.max_remediation_cycles:
            return {"final_status": "BUDGET_EXHAUSTED", "diagnosis": f"remediation cycles exhausted ({budget.max_remediation_cycles}): {reason}"}
        if counters.get(counter_key, 0) + 1 > budget.max_fixes_per_finding:
            return {"final_status": "FAILED_VERIFICATION", "diagnosis": f"fix attempts exhausted for {counter_key}: {reason}"}
        ctx.events.emit("remediation", target=target, findings=finding_ids, reason=reason, cycle=rem_cycle + 1)
        return {
            "remediation": {"target": target, "finding_ids": finding_ids, "reason": reason},
            "remediation_cycle": rem_cycle + 1,
            "remediation_counters": {counter_key: counters.get(counter_key, 0) + 1},
            "routing": [{"from": "triage", "to": target, "reason": reason, "cycle": rem_cycle + 1, "commit": commit}],
        }

    # 1. Architectural escalations from developers → MAGI subgraph (new round, new plan).
    esc = _escalations(state)
    if esc:
        round = state.get("architecture_round", 0)
        if round + 1 >= budget.max_architecture_rounds:
            return {"final_status": "UNRESOLVED_REQUIREMENT", "diagnosis": f"architectural escalation with no rounds left: {[f['title'] for f in esc]}"}
        ctx.events.emit("route", to="magi", reason="architecture escalation", findings=[f["meta"]["id"] for f in esc])
        ctx.workspaces.restore(ctx.run_id, state["baseline_commit"])
        return {
            "architecture_round": round + 1, "magi_mode": "revise", "selected_plan": None,
            "candidate_commit": state["baseline_commit"],
            "remediation": {"finding_ids": [f["meta"]["id"] for f in esc], "handled": [f["meta"]["id"] for f in esc]},
            "routing": [{"from": "triage", "to": "magi", "reason": "architecture escalation", "commit": commit}],
        }

    # 2. Verification (D) on this candidate?
    spec_done = all(_gate(state, g, commit) for g in ("qa.acceptance", "sre.review", "security.review"))
    if not _gate(state, "integrity.protected_paths", commit) or not spec_done:
        return {"routing": [{"from": "triage", "to": "verify", "commit": commit}], "remediation": {}}

    # 3. Failures on this candidate → fix loops.
    integrity = _gate(state, "integrity.protected_paths", commit)
    if integrity["verdict"] == Verdict.FAIL.value:
        return {"final_status": "POLICY_DENIED", "diagnosis": f"integrity violation: {integrity['detail']}"}
    failing = {g: _gate(state, g, commit) for g in FIXABLE_GATES | {"scope.write_scopes", "qa.acceptance"}}
    failing = {g: r for g, r in failing.items() if r and r["verdict"] == Verdict.FAIL.value}
    failing.update({k.split("@")[0]: r for k, r in (state.get("gate_results") or {}).items()
                    if k.startswith("qa.scenario:") and r["candidate_commit"] == commit and r["verdict"] == Verdict.FAIL.value})
    code_findings = _confirmed(state, subject=commit, categories={c.value for c in FindingCategory} - {
        FindingCategory.DOCUMENTATION.value, FindingCategory.ARCHITECTURE.value, FindingCategory.TOOLING.value})
    arch_findings = _confirmed(state, subject=commit, categories={FindingCategory.ARCHITECTURE.value})
    if arch_findings:
        round = state.get("architecture_round", 0)
        if round + 1 >= budget.max_architecture_rounds:
            return {"final_status": "FAILED_VERIFICATION", "diagnosis": f"confirmed architectural findings, no rounds left: {[f['title'] for f in arch_findings]}"}
        ctx.workspaces.restore(ctx.run_id, state["baseline_commit"])
        return {"architecture_round": round + 1, "magi_mode": "revise", "selected_plan": None, "candidate_commit": state["baseline_commit"],
                "remediation": {"finding_ids": [f["meta"]["id"] for f in arch_findings], "handled": [f["meta"]["id"] for f in arch_findings]},
                "routing": [{"from": "triage", "to": "magi", "reason": "confirmed architectural finding", "commit": commit}]}
    gate_items = [(k.split("@")[0], r) for k, r in (state.get("gate_results") or {}).items() if r["candidate_commit"] == commit]
    errors = sorted(g for g, r in gate_items if r["verdict"] == Verdict.ERROR.value)
    tooling = _confirmed(state, subject=commit, categories={FindingCategory.TOOLING.value})
    if errors or tooling:
        key = f"gate-error@{commit}"
        used = (state.get("technical_retries") or {}).get(key, 0)
        if used < budget.max_technical_retries:
            ctx.events.emit("technical_retry", stage="verification", gates=errors, tooling=[f["title"] for f in tooling], attempt=used + 1)
            scope = commit[:12]
            stale_gates = {k: None for k, r in (state.get("gate_results") or {}).items() if r["candidate_commit"] == commit}
            stale_stages = {k: None for k in (state.get("stage_results") or {}) if k.startswith((f"specialist:{scope}:", f"final:{scope}:"))}
            return {"technical_retries": {key: used + 1}, "gate_results": stale_gates, "stage_results": stale_stages,
                    "routing": [{"from": "triage", "to": "verify", "reason": "technical retry", "attempt": used + 1, "commit": commit}]}
        return {"final_status": "INFRA_FAILURE", "diagnosis": f"checks errored after {used} technical retries: {errors or [f['title'] for f in tooling]}"}
    if failing or code_findings:
        # Attribution: a pair_bug finding, or a check that passed on DEV1's candidate but
        # fails after DEV2 changed the code, goes back to the pair; everything else to DEV1.
        initial = state.get("dev1_initial_checks") or {}
        pair_introduced = state.get("last_code_author") == AgentId.DEV2.value and any(
            initial.get(g) == Verdict.PASS.value for g in failing
        )
        pair_bug = any(f["category"] == FindingCategory.PAIR_BUG.value for f in code_findings)
        target = AgentId.DEV2.value if (pair_bug or pair_introduced) else AgentId.DEV1.value
        ids = [f["meta"]["id"] for f in code_findings] + [r["meta"]["id"] for r in failing.values()]
        reason = f"failing checks {sorted(failing)}; confirmed findings {[f['title'] for f in code_findings]}"
        counter = "code:" + ",".join(sorted(failing) + sorted(f["title"] for f in code_findings))
        return fix(target, ids, reason, counter)

    # 4. Documentation (E) on this candidate. Docs commits produce a new candidate, so D
    #    reruns on it (all mandatory gates re-run on the final candidate).
    docs = [d for d in (state.get("documentation_results") or {}).values() if d.get("candidate_commit") == commit and d.get("ok")]
    if not docs:
        if state.get("docs_cycle", 0) >= budget.max_remediation_cycles + 1:
            return {"final_status": "BUDGET_EXHAUSTED", "diagnosis": "documentation cycles exhausted"}
        return {"docs_cycle": state.get("docs_cycle", 0) + 1, "routing": [{"from": "triage", "to": "docs", "commit": commit}]}

    # 5. Final MAGI review (F) on this candidate.
    final = _gate(state, "magi.final_review", commit)
    if not final:
        return {"routing": [{"from": "triage", "to": "final_review", "commit": commit}]}
    doc_findings = _confirmed(state, subject=commit, categories={FindingCategory.DOCUMENTATION.value})
    if doc_findings:
        counters = state.get("remediation_counters") or {}
        key = "docs:" + ",".join(sorted(f["title"] for f in doc_findings))
        if counters.get(key, 0) + 1 > budget.max_fixes_per_finding:
            return {"final_status": "FAILED_VERIFICATION", "diagnosis": f"documentation findings unresolved: {[f['title'] for f in doc_findings]}"}
        return {"docs_cycle": state.get("docs_cycle", 0) + 1, "remediation_counters": {key: counters.get(key, 0) + 1},
                "documentation_results": {k: {**d, "ok": False} for k, d in (state.get("documentation_results") or {}).items() if d.get("candidate_commit") == commit},
                "routing": [{"from": "triage", "to": "docs", "reason": "documentation finding", "commit": commit}]}
    if final["verdict"] == Verdict.FAIL.value:
        final_code = _confirmed(state, subject=commit)
        if final_code:
            return fix(AgentId.DEV1.value, [f["meta"]["id"] for f in final_code], "final review findings",
                       "final:" + ",".join(sorted(f["title"] for f in final_code)))
    return {"routing": [{"from": "triage", "to": "gate", "commit": commit}]}


def route_triage(state: dict) -> str:
    if state.get("final_status"):
        return "gate"
    last = (state.get("routing") or [{}])[-1]
    to = last.get("to")
    if last.get("from") == "triage":
        if to == "magi":
            return "magi"
        if to in (AgentId.DEV1.value, AgentId.DEV2.value):
            return "implementation"
        if to in ("verify", "docs", "final_review", "gate"):
            return {"verify": "verification", "docs": "documentation", "final_review": "final_review", "gate": "gate"}[to]
    return "gate"


# -- G: deterministic gate ---------------------------------------------------------------------


@node("gate")
def gate(state: dict, ctx: RunContext) -> dict:
    """SUCCESS only if every mandatory gate PASSed (or is a justified, allowed N/A) on the
    final candidate commit. Missing, UNKNOWN or ERROR checks are never PASS."""
    commit = state.get("candidate_commit") or state["baseline_commit"]
    checks: list[GateCheck] = []
    for name in ctx.policy.mandatory_gates:
        if name == "docs.updated":
            docs = [d for d in (state.get("documentation_results") or {}).values() if d.get("candidate_commit") == commit and d.get("ok")]
            if not docs:
                verdict, detail = Verdict.UNKNOWN, "documentation step not completed on the final candidate"
            elif docs[0].get("changed"):
                verdict, detail = Verdict.PASS, f"documentation updated: {docs[0]['changed']}"
            elif docs[0].get("no_update_reason") and ctx.policy.not_applicable_allowed(name):
                verdict, detail = Verdict.NOT_APPLICABLE, f"not applicable: {docs[0]['no_update_reason']}"
            else:
                verdict, detail = Verdict.FAIL, "no documentation update and no justification"
            checks.append(GateCheck(name=name, mandatory=True, verdict=verdict, candidate_commit=commit, detail=detail))
            continue
        r = _gate(state, name, commit)
        if r is None:
            checks.append(GateCheck(name=name, mandatory=True, verdict=Verdict.UNKNOWN, candidate_commit=commit,
                                    detail="check never executed on the final candidate"))
            continue
        verdict = Verdict(r["verdict"])
        if verdict is Verdict.NOT_APPLICABLE and not ctx.policy.not_applicable_allowed(name):
            verdict = Verdict.UNKNOWN
        checks.append(GateCheck(name=name, mandatory=True, verdict=verdict, candidate_commit=r["candidate_commit"],
                                detail=r["detail"][:1000], evidence_ref=r["meta"]["id"]))
    # Evidence must belong to this candidate; and the control plane must be unchanged now.
    fresh = ctx.fingerprint() == (state.get("control_fingerprint") or {})
    all_pass = all(c.verdict in (Verdict.PASS, Verdict.NOT_APPLICABLE) and c.candidate_commit == commit for c in checks)
    status, diagnosis = state.get("final_status"), state.get("diagnosis", "")
    if not fresh:
        status, diagnosis = "POLICY_DENIED", "control plane (policy/prompts/gates) changed during the run"
    elif status is None:
        if all_pass:
            status, diagnosis = "SUCCESS", "all mandatory gates passed on the final candidate (verified change only; not integrated or deployed)"
        else:
            bad = [f"{c.name}={c.verdict.value}" for c in checks if c.verdict not in (Verdict.PASS, Verdict.NOT_APPLICABLE)]
            status, diagnosis = "FAILED_VERIFICATION", f"mandatory gates not passed: {bad}"
    elif status == "SUCCESS" and not all_pass:  # defensive: nothing upstream may force SUCCESS
        status, diagnosis = "FAILED_VERIFICATION", "SUCCESS requested without passing gates"
    ctx.events.emit("gate", status=status, commit=commit, checks=[c.model_dump(mode="json") for c in checks])
    return {"final_status": status, "diagnosis": diagnosis, "gate_checks": [c.model_dump(mode="json") for c in checks],
            "routing": [{"from": "gate", "to": "end", "status": status}]}
