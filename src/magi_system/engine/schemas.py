"""Structured outputs requested from agents ("drafts").

Drafts are what an LLM may produce. Code turns them into artifacts (adding provenance,
IDs, verification status). Agents never set statuses, verdicts or IDs themselves.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from magi_system.models.knowledge import ClaimKind


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimDraft(Draft):
    kind: ClaimKind
    statement: str
    target: str
    pattern: str = ""
    expect: bool = True
    critical: bool = False

    @model_validator(mode="before")
    @classmethod
    def _repair_bare_symbol_path(cls, data: Any) -> Any:
        # Models write the symbol in `pattern` ("def verify_credentials") and only the path in `target`. The information is
        # there, so complete the target instead of discarding a true claim.
        if isinstance(data, dict) and data.get("kind") == ClaimKind.SYMBOL_EXISTS.value and "::" not in str(data.get("target", "")):
            m = re.match(r"\s*(?:async\s+def|def|class)\s+(\w+)", str(data.get("pattern", "")))
            if m:
                return {**data, "target": f"{data['target']}::{m.group(1)}"}
        return data

    @model_validator(mode="after")
    def _target_matches_the_kind(self) -> ClaimDraft:
        # A claim in the wrong format says nothing about the code, and the verifier would call it "refuted". Failing
        # here reports the mistake to the agent, which retries (AgentCaller.ask feeds this message back).
        if self.kind is ClaimKind.SYMBOL_EXISTS and "::" not in self.target:
            raise ValueError(f"symbol claim target {self.target!r} must be 'path::symbol' (or '*::symbol'), not a bare path")
        if self.kind is ClaimKind.POLICY_EXISTS and any(ch.isspace() for ch in self.target):
            raise ValueError(f"policy claim target {self.target!r} must be one dotted key of the platform policy "
                             "(for example 'limits.max_tool_calls'), without spaces")
        return self


class AssumptionDraft(Draft):
    text: str
    critical: bool = False
    supporting_claims: list[int] = Field(default_factory=list, description="indexes into this draft's claims")


class RequirementDraft(Draft):
    text: str
    kind: Literal["explicit", "derived"]
    source_quote: str = Field("", description="verbatim excerpt of the request (explicit only)")
    derived_from_quotes: list[str] = Field(default_factory=list, description="verbatim request excerpts it derives from")
    mandatory: bool = True


class AnalysisDraft(Draft):
    understanding: str
    requirements: list[RequirementDraft]
    claims: list[ClaimDraft] = Field(default_factory=list)
    assumptions: list[AssumptionDraft] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class CoverageDraft(Draft):
    requirement_id: str
    covered: bool
    how: str


class ChangeDraft(Draft):
    path: str
    action: Literal["create", "modify", "delete"]
    description: str


class ProposalDraft(Draft):
    title: str
    summary: str
    coverage: list[CoverageDraft]
    claims: list[ClaimDraft] = Field(default_factory=list)
    assumptions: list[AssumptionDraft] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    contracts: list[str] = Field(default_factory=list)
    changes: list[ChangeDraft]
    verification_plan: list[str]
    migration_rollback: str
    documentation_impact: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    post_implementation_obligations: list[str] = Field(default_factory=list)


class FindingDraft(Draft):
    category: Literal[
        "design", "local_bug", "pair_bug", "architecture", "documentation", "security", "operations", "tooling"
    ]
    severity: Literal["info", "minor", "major", "blocker"]
    title: str
    detail: str
    claims: list[ClaimDraft] = Field(default_factory=list)


class ScoreDraft(Draft):
    criterion: str
    score: int = Field(ge=0, le=5)
    rationale: str


class AssessmentDraft(Draft):
    label: str = Field(description="masked proposal label, e.g. 'Proposal A'")
    scores: list[ScoreDraft]
    supports: bool
    findings: list[FindingDraft] = Field(default_factory=list)


class CrossReviewDraft(Draft):
    assessments: list[AssessmentDraft]


class ProposalFindingsDraft(Draft):
    label: str
    findings: list[FindingDraft] = Field(default_factory=list)


class AcceptanceScenarioDraft(Draft):
    id: str
    description: str
    requirement_ids: list[str] = Field(default_factory=list)
    command: str = Field("", description="allowlisted command whose exit code 0 means the scenario passes")


class PreventiveReviewDraft(Draft):
    reviews: list[ProposalFindingsDraft]
    acceptance_scenarios: list[AcceptanceScenarioDraft] = Field(default_factory=list)  # QA only
    documentation_impact: list[str] = Field(default_factory=list)  # Tech Writer only
    not_applicable_reason: str = ""  # SRE: why operational review does not apply, if so


class ToolAction(Draft):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class HandoffDraft(Draft):
    summary: str
    checks_run: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    escalations: list[FindingDraft] = Field(default_factory=list)


class PairAssessmentDraft(Draft):
    assessment: str
    concerns: list[str] = Field(default_factory=list)


class CandidateReviewDraft(Draft):
    findings: list[FindingDraft] = Field(default_factory=list)
    acceptance_scenarios: list[AcceptanceScenarioDraft] = Field(
        default_factory=list, description="QA only: a corrected version of a scenario that FAILED, with the same id")
    not_applicable_reason: str = ""
    summary: str = ""


class DocsDraft(Draft):
    summary: str
    updates: list[dict[str, str]] = Field(default_factory=list, description="[{path, kind, summary}]")
    not_documented_as_implemented: list[str] = Field(default_factory=list)
    no_update_reason: str = ""


def turn_schema(final: type[Draft]) -> type[BaseModel]:
    """Envelope for tool-using agents: either tool actions, or a final answer."""
    return create_model(
        f"Turn{final.__name__}",
        __config__=ConfigDict(extra="forbid"),
        actions=(list[ToolAction], Field(default_factory=list)),
        final=(final | None, None),
    )
