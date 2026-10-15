"""Shared primitives: enums, artifact metadata, stable identifiers, hashing."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for all platform models: immutable, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


def canonical_json(value: Any) -> str:
    """Deterministic JSON used for hashing and identity."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic ID from content parts, so re-execution after resume yields the same IDs."""
    digest = hashlib.sha256(canonical_json(list(parts)).encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Phase(StrEnum):
    INTAKE = "intake"
    MAGI_ANALYSIS = "magi_analysis"
    MAGI_PROPOSAL = "magi_proposal"
    MAGI_CROSS_REVIEW = "magi_cross_review"
    PREVENTIVE_REVIEW = "preventive_review"
    CLAIM_VERIFICATION = "claim_verification"
    SELECTION = "selection"
    IMPLEMENTATION = "implementation"
    PAIRING = "pairing"
    SPECIALIST_VERIFICATION = "specialist_verification"
    REMEDIATION = "remediation"
    DOCUMENTATION = "documentation"
    FINAL_REVIEW = "final_review"
    GATE = "gate"


class AgentId(StrEnum):
    """Stable internal identifiers, distinct from display names."""

    MELCHIOR = "magi.melchior"
    BALTHASAR = "magi.balthasar"
    CASPER = "magi.casper"
    DEV1 = "dev.writer"
    DEV2 = "dev.pair"
    QA = "spec.qa"
    SRE = "spec.sre"
    SECURITY = "spec.security"
    TECH_WRITER = "doc.tech_writer"
    VERIFIER = "sys.verifier"
    PLATFORM = "sys.platform"


MAGI_IDS: tuple[AgentId, ...] = (AgentId.MELCHIOR, AgentId.BALTHASAR, AgentId.CASPER)

DISPLAY_NAMES: dict[AgentId, str] = {
    AgentId.MELCHIOR: "Melchior-Magi 1",
    AgentId.BALTHASAR: "Balthasar-Magi 2",
    AgentId.CASPER: "Casper-Magi 3",
    AgentId.DEV1: "DEV1 Writer",
    AgentId.DEV2: "DEV2 Pair",
    AgentId.QA: "QA",
    AgentId.SRE: "SRE",
    AgentId.SECURITY: "Security",
    AgentId.TECH_WRITER: "Tech Writer",
    AgentId.VERIFIER: "Verifier",
    AgentId.PLATFORM: "Platform",
}


class ArtifactMeta(StrictModel):
    """Provenance carried by every artifact (brief §6)."""

    id: str
    producer: AgentId
    round: int = Field(ge=0)
    phase: Phase
    plan_version: int = Field(default=0, ge=0)
    input_hash: str
    evidence_refs: tuple[str, ...] = ()


class Artifact(StrictModel):
    meta: ArtifactMeta

    @property
    def id(self) -> str:
        return self.meta.id
