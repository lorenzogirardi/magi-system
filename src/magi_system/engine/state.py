"""Graph state (brief §6). JSON-native: artifacts are stored as dicts keyed by stable id
and merged by id, so concurrent fan-out writes never lose results and re-delivered
writes (subgraph → parent, resume) are idempotent.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def merge_by_id(left: dict | None, right: dict | None) -> dict:
    """Union of two id→artifact maps; the right-hand write wins for the same id, and a
    `None` value deletes the key (explicit invalidation).

    Used for artifacts whose later versions supersede earlier ones (e.g. a finding whose
    status moves OPEN → CONFIRMED). Producers write unique ids per (round, agent, phase).
    """
    out = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def max_counters(left: dict | None, right: dict | None) -> dict:
    """Monotonic counters merged by maximum: safe under concurrent and replayed writes."""
    out = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            continue
        out[key] = value if key not in out or out[key] is None else max(out[key], value)
    return out


def append_unique(left: list | None, right: list | None) -> list:
    """Ordered list without duplicates (used for the routing trail)."""
    out = list(left or [])
    seen = {repr(x) for x in out}
    for item in right or []:
        if repr(item) not in seen:
            out.append(item)
            seen.add(repr(item))
    return out


class MagiState(TypedDict, total=False):
    # identity / inputs
    run_id: str
    original_request: dict
    baseline_commit: str
    target_identity: str
    policy_version: str
    policy_sha256: str
    project_config: dict
    repository_snapshot: dict

    # knowledge
    requirements: Annotated[dict, merge_by_id]
    evidence: Annotated[dict, merge_by_id]
    claims: Annotated[dict, merge_by_id]
    verification_results: Annotated[dict, merge_by_id]

    # MAGI decision
    analyses: Annotated[dict, merge_by_id]  # "r{round}:{agent}" → analysis | failure
    proposals: Annotated[dict, merge_by_id]
    assessments: Annotated[dict, merge_by_id]
    findings: Annotated[dict, merge_by_id]
    stage_results: Annotated[dict, merge_by_id]  # "{stage}:{scope}:{agent}" → {ok, ...} | failure
    initial_assessments: Annotated[dict, merge_by_id]
    acceptance_scenarios: Annotated[dict, merge_by_id]
    decisions: Annotated[dict, merge_by_id]
    masked_labels: Annotated[dict, merge_by_id]  # proposal id → label (per round)
    architecture_round: int
    magi_mode: str  # "analyze" | "revise"
    selected_plan: str | None
    plan_version: int

    # implementation & verification
    candidate_commit: str
    task_results: Annotated[dict, merge_by_id]  # tasks, handoffs, change sets
    specialist_results: Annotated[dict, merge_by_id]
    documentation_results: Annotated[dict, merge_by_id]
    gate_results: Annotated[dict, merge_by_id]  # "{gate}@{commit}" → VerificationResult
    remediation: dict  # current remediation request {target, finding_ids, reason}
    remediation_cycle: int
    remediation_counters: Annotated[dict, max_counters]
    technical_retries: Annotated[dict, max_counters]

    # budget & outcome
    budget_used: Annotated[dict, max_counters]
    routing: Annotated[list, append_unique]
    final_status: str | None
    diagnosis: str
    gate_checks: list[dict]
    control_fingerprint: dict
    last_code_author: str  # agent id of the latest code (non-doc) change
    dev1_initial_checks: dict  # gate → verdict on DEV1's latest candidate
    docs_cycle: int

    # transient fan-out payload (only present inside Send-dispatched nodes)
    fanout: dict


INITIAL_KEYS: dict[str, Any] = {
    "requirements": {},
    "evidence": {},
    "claims": {},
    "verification_results": {},
    "analyses": {},
    "proposals": {},
    "assessments": {},
    "findings": {},
    "stage_results": {},
    "initial_assessments": {},
    "acceptance_scenarios": {},
    "decisions": {},
    "masked_labels": {},
    "architecture_round": 0,
    "magi_mode": "analyze",
    "selected_plan": None,
    "plan_version": 0,
    "task_results": {},
    "specialist_results": {},
    "documentation_results": {},
    "gate_results": {},
    "remediation": {},
    "remediation_cycle": 0,
    "remediation_counters": {},
    "technical_retries": {},
    "budget_used": {},
    "routing": [],
    "final_status": None,
    "diagnosis": "",
    "gate_checks": [],
    "docs_cycle": 0,
}
