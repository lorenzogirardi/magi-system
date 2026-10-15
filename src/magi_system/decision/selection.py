"""Deterministic MAGI decision logic (brief §8). Pure functions over validated artifacts.

Invariants implemented here:
- votes never prove facts: `supports` flags never change finding status or admissibility;
- unanimity does not replace verification: admissibility only looks at verified state;
- an objection is not a veto: only CONFIRMED blockers block;
- a confirmed blocker is not overridden by majority;
- UNKNOWN is not PASS: critical claims that are unverifiable stay unresolved unknowns;
- no compensation: an inadmissible proposal is never ranked, whatever its scores.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from magi_system.models.common import AgentId
from magi_system.models.decision import (
    ArchitectureProposal,
    Finding,
    FindingStatus,
    ProposalAssessment,
    ProposalStanding,
    SelectionOutcome,
    Severity,
)
from magi_system.models.knowledge import ClaimStatus, Requirement

MAGI_ORDER = (AgentId.MELCHIOR, AgentId.BALTHASAR, AgentId.CASPER)


def resolve_finding_status(finding: Finding, claim_status: Mapping[str, ClaimStatus]) -> tuple[FindingStatus, str]:
    """A finding is CONFIRMED only if all its claims are SUPPORTED by tools.

    - any claim REFUTED → REFUTED (invented objection);
    - no claims → UNRESOLVED (an assertion without evidence; never a veto);
    - any claim UNVERIFIABLE → UNRESOLVED.
    """
    if not finding.claims:
        return FindingStatus.UNRESOLVED, "no checkable claims supplied"
    statuses = [claim_status.get(c.id, c.status) for c in finding.claims]
    if any(s is ClaimStatus.REFUTED for s in statuses):
        return FindingStatus.REFUTED, "a supporting claim was refuted by verification"
    if all(s is ClaimStatus.SUPPORTED for s in statuses):
        return FindingStatus.CONFIRMED, "all supporting claims verified"
    return FindingStatus.UNRESOLVED, "supporting claims could not be verified"


def admissibility(
    proposal: ArchitectureProposal,
    *,
    requirements: Iterable[Requirement],
    findings: Iterable[Finding],
    claim_status: Mapping[str, ClaimStatus],
    allowed_paths_ok: bool = True,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    covered = {c.requirement_id for c in proposal.coverage if c.covered}
    for req in requirements:
        if req.mandatory and req.id not in covered:
            reasons.append(f"mandatory requirement not covered: {req.id}")
    if not proposal.changes:
        reasons.append("proposal plans no changes")
    if not proposal.verification_plan:
        reasons.append("proposal has no verification plan")
    if not allowed_paths_ok:
        reasons.append("proposal plans changes to protected or out-of-scope paths")
    for claim in proposal.claims:
        status = claim_status.get(claim.id, claim.status)
        if status is ClaimStatus.REFUTED and claim.critical:
            reasons.append(f"critical claim refuted: {claim.statement}")
        elif status in (ClaimStatus.UNVERIFIABLE, ClaimStatus.UNVERIFIED) and claim.critical:
            reasons.append(f"critical claim unresolved: {claim.statement}")
    for assumption in proposal.assumptions:
        if assumption.critical and not assumption.resolved:
            reasons.append(f"critical unknown unresolved: {assumption.text}")
    for f in findings:
        if f.subject_id == proposal.id and f.severity is Severity.BLOCKER and f.status is FindingStatus.CONFIRMED:
            reasons.append(f"confirmed blocker: {f.title}")
    return (not reasons), tuple(reasons)


def score_proposal(
    proposal: ArchitectureProposal, assessments: Iterable[ProposalAssessment], rubric: Mapping[str, float]
) -> float | None:
    """Weighted mean of peer scores, excluding the author's own assessment.

    Returns None when no peer scored the proposal. Criteria outside the rubric are ignored;
    missing criteria count as 0 (an assessor skipping a criterion gains nothing).
    """
    total_weight = sum(rubric.values())
    per_reviewer = []
    for a in assessments:
        if a.proposal_id != proposal.id or a.reviewer == proposal.author:
            continue
        scores = {s.criterion: s.score for s in a.scores}
        per_reviewer.append(sum(w * scores.get(c, 0) for c, w in rubric.items()) / total_weight)
    if not per_reviewer:
        return None
    return round(sum(per_reviewer) / len(per_reviewer), 4)


def _tie_key(proposal: ArchitectureProposal, rules: Iterable[str]) -> tuple:
    key = []
    for rule in rules:
        if rule == "fewer_planned_changes":
            key.append(len(proposal.changes))
        elif rule == "fewer_assumptions":
            key.append(len(proposal.assumptions))
        elif rule == "magi_order":
            key.append(MAGI_ORDER.index(proposal.author) if proposal.author in MAGI_ORDER else 99)
        else:
            raise ValueError(f"unknown tie-break rule {rule!r}")
    key.append(proposal.id)  # total order: fully deterministic
    return tuple(key)


def rank(
    proposals: list[ArchitectureProposal],
    *,
    requirements: list[Requirement],
    findings: list[Finding],
    assessments: list[ProposalAssessment],
    claim_status: Mapping[str, ClaimStatus],
    rubric: Mapping[str, float],
    tie_break: list[str],
    tie_epsilon: float,
    path_ok: Mapping[str, bool] | None = None,
) -> tuple[SelectionOutcome, str | None, list[ProposalStanding], str]:
    standings: list[ProposalStanding] = []
    admissible: list[tuple[ArchitectureProposal, float]] = []
    for p in proposals:
        ok, reasons = admissibility(
            p, requirements=requirements, findings=findings, claim_status=claim_status,
            allowed_paths_ok=(path_ok or {}).get(p.id, True),
        )
        score = score_proposal(p, assessments, rubric) if ok else None
        if ok and score is None:
            ok, reasons = False, ("no peer assessment available",)
        standings.append(ProposalStanding(proposal_id=p.id, admissible=ok, reasons=reasons, score=score))
        if ok:
            admissible.append((p, score))  # type: ignore[arg-type]
    if not admissible:
        return SelectionOutcome.NO_ADMISSIBLE, None, standings, ""

    best_score = max(s for _, s in admissible)
    contenders = [p for p, s in admissible if best_score - s <= tie_epsilon]
    ordered_admissible = sorted(admissible, key=lambda ps: (-ps[1], _tie_key(ps[0], tie_break)))
    tie_note = ""
    if len(contenders) > 1:
        contenders.sort(key=lambda p: _tie_key(p, tie_break))
        winner = contenders[0]
        tie_note = f"scores within {tie_epsilon}: tie-break {tie_break} chose {winner.id}"
        ordered_admissible.sort(key=lambda ps: (ps[0].id != winner.id, -ps[1], _tie_key(ps[0], tie_break)))
    winner = ordered_admissible[0][0]
    ranks = {p.id: i + 1 for i, (p, _) in enumerate(ordered_admissible)}
    standings = [s.model_copy(update={"rank": ranks.get(s.proposal_id)}) for s in standings]
    return SelectionOutcome.SELECTED, winner.id, standings, tie_note


def disputed_claims_affecting_winner(
    winner_id: str, proposals: list[ArchitectureProposal], findings: list[Finding], claim_status: Mapping[str, ClaimStatus]
) -> list[str]:
    """Claim ids still UNVERIFIED that could change the winner (must be verified first)."""
    out = []
    for p in proposals:
        if p.id != winner_id:
            continue
        for c in p.claims:
            if claim_status.get(c.id, c.status) is ClaimStatus.UNVERIFIED:
                out.append(c.id)
    for f in findings:
        if f.subject_id == winner_id and f.severity in (Severity.BLOCKER, Severity.MAJOR):
            out += [c.id for c in f.claims if claim_status.get(c.id, c.status) is ClaimStatus.UNVERIFIED]
    return sorted(set(out))
