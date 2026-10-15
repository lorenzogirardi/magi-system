"""Implementation tasks, change sets, handoffs and documentation (brief §4, §9)."""

from __future__ import annotations

from enum import StrEnum

from magi_system.models.common import AgentId, Artifact, StrictModel


class ImplementationTask(Artifact):
    title: str
    description: str
    allowed_paths: tuple[str, ...]  # globs, intersected with policy write scopes
    finding_id: str = ""  # set for remediation tasks
    owner: AgentId


class FileChange(StrictModel):
    path: str
    action: str  # create | modify | delete
    before_sha256: str = ""
    after_sha256: str = ""


class ChangeSet(Artifact):
    author: AgentId
    base_commit: str
    commit: str  # resulting candidate commit ("" when no changes)
    files: tuple[FileChange, ...]
    patch_path: str  # artifact path of the exported patch
    empty: bool


class HandoffStatus(StrEnum):
    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    ESCALATED = "escalated"
    FAILED = "failed"


class AgentHandoff(Artifact):
    author: AgentId
    status: HandoffStatus
    summary: str
    changeset_id: str = ""
    checks_run: tuple[str, ...] = ()
    deviations: tuple[str, ...] = ()
    escalations: tuple[str, ...] = ()  # finding ids raised to MAGI
    initial_assessment: str = ""  # DEV2: written before reading DEV1 handoff


class DocKind(StrEnum):
    TUTORIAL = "tutorial"
    HOW_TO = "how-to"
    REFERENCE = "reference"
    EXPLANATION = "explanation"
    ADR = "adr"
    RUNBOOK = "runbook"
    CHANGELOG = "changelog"


class DocUpdate(StrictModel):
    path: str
    kind: DocKind
    summary: str


class DocumentationResult(Artifact):
    candidate_commit: str
    impact: tuple[str, ...]
    updates: tuple[DocUpdate, ...]
    changeset_id: str = ""
    planned_only_mentions: tuple[str, ...] = ()  # items deliberately NOT documented as implemented
