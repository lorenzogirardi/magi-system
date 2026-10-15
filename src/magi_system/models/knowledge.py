"""Request, requirements, evidence, claims and assumptions (brief §6, §8)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from magi_system.models.common import Artifact, StrictModel


class Request(StrictModel):
    """The original change request, verbatim. Never rewritten by agents."""

    text: str = Field(min_length=1)
    source: str = "request-file"
    sha256: str


class RequirementKind(StrEnum):
    EXPLICIT = "explicit"  # stated in the request text
    DERIVED = "derived"  # interpretation, must trace back to explicit text or evidence


class Requirement(Artifact):
    kind: RequirementKind
    text: str
    mandatory: bool = True
    source_quote: str = ""  # verbatim excerpt from the request for EXPLICIT
    derived_from: tuple[str, ...] = ()  # requirement / evidence ids for DERIVED

    @model_validator(mode="after")
    def _traceable(self) -> Requirement:
        if self.kind is RequirementKind.EXPLICIT and not self.source_quote.strip():
            raise ValueError("explicit requirement needs a source_quote from the request")
        if self.kind is RequirementKind.DERIVED and not self.derived_from:
            raise ValueError("derived requirement needs derived_from references")
        return self


class EvidenceKind(StrEnum):
    FILE = "file"  # file exists, optional line range / content hash
    SYMBOL = "symbol"  # symbol found in a file
    COMMAND = "command"  # command executed through the runner
    DOCUMENT = "document"
    POLICY = "policy"
    PACKAGE = "package"  # installed dependency version


class Evidence(Artifact):
    """A checkable fact gathered by a tool, never by an LLM statement."""

    kind: EvidenceKind
    locator: str  # path, path#symbol, command, policy key, package name
    summary: str
    content_hash: str = ""
    target_commit: str = ""  # commit the evidence was gathered on
    collected_by_tool: str  # tool name that produced it


class ClaimKind(StrEnum):
    FILE_EXISTS = "file_exists"
    SYMBOL_EXISTS = "symbol_exists"
    TEXT_PRESENT = "text_present"  # pattern present in a file
    PACKAGE_VERSION = "package_version"  # dependency available (optionally >= version)
    POLICY_EXISTS = "policy_exists"  # policy key exists and is applicable
    COMMAND_SUCCEEDS = "command_succeeds"
    OPERATIONAL_GUARANTEE = "operational_guarantee"  # not mechanically checkable
    BUSINESS_ASSUMPTION = "business_assumption"  # never self-authorized by agents


MECHANICAL_CLAIM_KINDS = frozenset(
    {
        ClaimKind.FILE_EXISTS,
        ClaimKind.SYMBOL_EXISTS,
        ClaimKind.TEXT_PRESENT,
        ClaimKind.PACKAGE_VERSION,
        ClaimKind.POLICY_EXISTS,
        ClaimKind.COMMAND_SUCCEEDS,
    }
)


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"


class Claim(StrictModel):
    """A factual statement an agent relies on. Verified by tools (sys.verifier)."""

    id: str
    kind: ClaimKind
    statement: str
    target: str  # path, path::symbol, package[>=ver], policy key, command
    pattern: str = ""  # for TEXT_PRESENT
    expect: bool = True  # False asserts absence (e.g. "symbol X does not exist")
    critical: bool = False
    evidence_refs: tuple[str, ...] = ()
    status: ClaimStatus = ClaimStatus.UNVERIFIED


class Assumption(StrictModel):
    id: str
    text: str
    critical: bool = False
    resolved: bool = False
    resolution: str = ""
    depends_on_claims: tuple[str, ...] = ()


class FileEntry(StrictModel):
    path: str
    size: int
    blob: str  # git blob id at the snapshot commit


class RepositorySnapshot(Artifact):
    target_identity: str
    commit: str
    files: tuple[FileEntry, ...]
    languages: tuple[str, ...]
    manifests: tuple[str, ...]  # package.json, pyproject.toml, Makefile, ...
    doc_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    project_config_source: str  # ".magi/config.yaml" or "discovery"

    def has_file(self, path: str) -> bool:
        return any(f.path == path for f in self.files)


class PolicySnapshot(StrictModel):
    """Effective policy for the run, frozen at start and hashed for resume checks."""

    policy_version: str
    profile: str
    effective: dict
    sha256: str
