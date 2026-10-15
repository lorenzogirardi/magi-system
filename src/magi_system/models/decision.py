"""MAGI analysis, proposals, findings, verification and selection (brief §7, §8)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from magi_system.models.common import AgentId, Artifact, StrictModel, Verdict
from magi_system.models.knowledge import Assumption, Claim


class FailureKind(StrEnum):
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"
    POLICY_VIOLATION = "policy_violation"


class AgentFailure(StrictModel):
    """Typed failure of an agent step; collected by fan-in like any result."""

    agent: AgentId
    round: int
    phase: str
    kind: FailureKind
    detail: str


class ArchitectAnalysis(Artifact):
    understanding: str
    requirement_ids: tuple[str, ...]
    claims: tuple[Claim, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    open_questions: tuple[str, ...] = ()  # factual gaps to resolve with tools
    risks: tuple[str, ...] = ()


class RequirementCoverage(StrictModel):
    requirement_id: str
    covered: bool
    how: str


class PlannedChange(StrictModel):
    path: str
    action: str = Field(pattern="^(create|modify|delete)$")
    description: str


class ArchitectureProposal(Artifact):
    """Complete proposal (brief §7). `version` increments on revision or merge."""

    author: AgentId
    version: int = 1
    title: str
    summary: str
    coverage: tuple[RequirementCoverage, ...]
    claims: tuple[Claim, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    components: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()
    changes: tuple[PlannedChange, ...]
    verification_plan: tuple[str, ...]
    migration_rollback: str
    documentation_impact: tuple[str, ...] = ()
    tradeoffs: tuple[str, ...] = ()
    post_implementation_obligations: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()  # source proposal ids when merged


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    REFUTED = "REFUTED"
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"


class FindingCategory(StrEnum):
    DESIGN = "design"
    LOCAL_BUG = "local_bug"
    PAIR_BUG = "pair_bug"
    ARCHITECTURE = "architecture"
    DOCUMENTATION = "documentation"
    SECURITY = "security"
    OPERATIONS = "operations"
    TOOLING = "tooling"  # tool/environment error → technical retry


class Finding(Artifact):
    """An objection or issue. Not a veto by itself: only CONFIRMED blockers block."""

    subject_id: str  # proposal id, candidate commit, doc path ...
    category: FindingCategory
    severity: Severity
    title: str
    detail: str
    claims: tuple[Claim, ...] = ()  # facts the finding relies on
    status: FindingStatus = FindingStatus.OPEN
    status_reason: str = ""
    owner: AgentId | None = None


class VerificationResult(Artifact):
    """Outcome of a tool-based check on a claim, or of a gate on a candidate."""

    subject_id: str  # claim id or gate name
    check: str
    verdict: Verdict
    detail: str
    candidate_commit: str = ""  # binds candidate-level evidence to one commit
    command: str = ""
    exit_code: int | None = None


class CriterionScore(StrictModel):
    criterion: str
    score: int = Field(ge=0, le=5)
    rationale: str


class ProposalAssessment(Artifact):
    """One MAGI scoring another proposal under a masked label."""

    reviewer: AgentId
    proposal_id: str
    masked_label: str
    scores: tuple[CriterionScore, ...]
    supports: bool
    finding_ids: tuple[str, ...] = ()


class ProposalStanding(StrictModel):
    proposal_id: str
    admissible: bool
    reasons: tuple[str, ...]
    score: float | None = None  # weighted mean; NOT a probability of correctness
    rank: int | None = None


class SelectionOutcome(StrEnum):
    SELECTED = "selected"
    REVISE = "revise"
    NO_ADMISSIBLE = "no_admissible"
    INSUFFICIENT_RESULTS = "insufficient_results"


class SelectionDecision(Artifact):
    outcome: SelectionOutcome
    selected_proposal_id: str | None
    standings: tuple[ProposalStanding, ...]
    tie_break_applied: str = ""
    rationale: str
