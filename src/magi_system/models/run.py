"""Run contract (brief §0.2), budget and outcome (brief §12, §13)."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator

from magi_system.models.common import StrictModel, Verdict

_SCP_LIKE = re.compile(r"^(?P<user>[^@/:]+)@(?P<host>[^:/]+):(?P<path>.+)$")


def redact_locator(locator: str) -> str:
    """Strip credentials (userinfo, query, fragment) from a repo locator before persisting.

    `git@host:path` keeps the conventional `git` user only, since it is not a secret.
    """
    m = _SCP_LIKE.match(locator)
    if m and "://" not in locator:
        user = "git" if m["user"] == "git" else "***"
        return f"{user}@{m['host']}:{m['path']}"
    parts = urlsplit(locator)
    if parts.scheme and parts.netloc:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    return locator


def locator_has_credentials(locator: str) -> bool:
    """True if the locator embeds a secret: a password, a token used as user name, or a
    query string. The conventional `git` SSH user is not a secret."""
    m = _SCP_LIKE.match(locator)
    if m and "://" not in locator:
        return False  # user@host:path carries no password; the user name is not a secret
    parts = urlsplit(locator)
    if not (parts.scheme and parts.netloc):
        return False
    return bool(parts.password or parts.query or (parts.username and parts.username != "git"))


class LocatorKind(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class RepoLocator(StrictModel):
    kind: LocatorKind
    value: str  # redacted; never contains credentials

    @field_validator("value")
    @classmethod
    def _no_credentials(cls, v: str) -> str:
        if locator_has_credentials(v) or v != redact_locator(v):
            raise ValueError("repository locator must not contain credentials")
        return v


class PublicationMode(StrEnum):
    """v1 publishes locally only. Push / pull request / merge / deploy are not implemented and
    are never decided by agents; they belong to an externally authorized step."""

    LOCAL_CANDIDATE = "local_candidate"
    PATCH = "patch"


class RunBudget(StrictModel):
    max_architecture_rounds: int = Field(default=2, ge=1)
    max_fixes_per_finding: int = Field(default=2, ge=0)
    max_remediation_cycles: int = Field(default=3, ge=0)
    max_technical_retries: int = Field(default=2, ge=0)
    max_duration_seconds: float = Field(default=3600.0, gt=0)
    max_tool_calls: int = Field(default=500, ge=1)
    max_llm_calls: int = Field(default=200, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)  # enforced only when measurable
    max_cost_usd: float | None = Field(default=None, gt=0)  # enforced only when reported


class ModelPrice(StrictModel):
    """USD per million tokens. When set, the run cost is computed from it instead of the provider's figure."""

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cached_input_per_mtok: float | None = Field(default=None, ge=0)  # prompt tokens served from cache; default: input price


class AgentModelConfig(StrictModel):
    """Per-agent provider settings. No secrets: keys come from environment at runtime."""

    provider: str = "fake"
    model: str = "scripted"
    temperature: float = 0.0
    max_output_tokens: int = 8192
    timeout_seconds: float = 120.0
    # OpenRouter only, passed through to the request: `routing` -> `provider` (e.g.
    # {"sort": "throughput"}), `reasoning` -> `reasoning` (e.g. {"effort": "low"}).
    routing: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None
    price: ModelPrice | None = None  # OpenRouter only; see ModelPrice


class RunInput(StrictModel):
    repo: RepoLocator
    requested_ref: str
    resolved_commit: str
    request_text: str
    request_sha256: str
    project_identity: str
    policy_profile: str
    budget: RunBudget
    agents: dict[str, AgentModelConfig]
    publication: PublicationMode = PublicationMode.LOCAL_CANDIDATE
    include_uncommitted: bool = False
    uncommitted_patch_sha256: str = ""  # set when include_uncommitted


class TerminalStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    UNRESOLVED_REQUIREMENT = "UNRESOLVED_REQUIREMENT"
    POLICY_DENIED = "POLICY_DENIED"
    INFRA_FAILURE = "INFRA_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class GateCheck(StrictModel):
    name: str
    mandatory: bool
    verdict: Verdict
    candidate_commit: str
    detail: str
    evidence_ref: str = ""


class ChangeLevel(StrEnum):
    """Report distinguishes these explicitly (brief §13). v1 reaches at most VERIFIED."""

    NONE = "none"
    VERIFIED = "verified"
    INTEGRATED = "integrated"
    DEPLOYED = "deployed"


class PublicationRecord(StrictModel):
    operation: str  # e.g. "export_patch", "local_branch"
    performed: bool
    location: str
    dedup_key: str


class RunOutcome(StrictModel):
    run_id: str
    target_identity: str
    baseline_commit: str
    final_commit: str
    plan_version: int
    status: TerminalStatus
    diagnosis: str
    gate_checks: tuple[GateCheck, ...]
    change_level: ChangeLevel
    diff_ref: str = ""
    patch_ref: str = ""
    documentation_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    publications: tuple[PublicationRecord, ...] = ()
