"""Platform policy engine.

Precedence (brief §0.3): platform policy > authorized run profile > project config >
agent inferences. Profiles are defined inside the platform policy, so selecting one is
an authorized choice of the caller; project config can never widen what they allow.
"""

from __future__ import annotations

import copy
import shlex
from dataclasses import dataclass
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

import yaml

from magi_system.models.common import AgentId, content_hash
from magi_system.models.knowledge import PolicySnapshot


class PolicyError(Exception):
    """Raised when a requested action or configuration is denied by policy."""


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_platform_policy(text: str | None = None) -> dict:
    if text is None:
        text = resources.files("magi_system.policy").joinpath("platform_policy.yaml").read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "policy_version" not in data:
        raise PolicyError("platform policy is malformed")
    return data


READ_ONLY_ROLES = frozenset(
    {
        AgentId.MELCHIOR,
        AgentId.BALTHASAR,
        AgentId.CASPER,
        AgentId.QA,
        AgentId.SRE,
        AgentId.SECURITY,
        AgentId.VERIFIER,
    }
)
CODE_WRITER_ROLES = frozenset({AgentId.DEV1, AgentId.DEV2})
DOC_WRITER_ROLES = frozenset({AgentId.TECH_WRITER})


def normalize_rel_path(path: str) -> str:
    """Repo-relative POSIX path; rejects absolute paths and traversal."""
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or any(part == ".." for part in p.parts) or str(p) in ("", "."):
        raise PolicyError(f"path not allowed: {path!r}")
    return str(p)


def matches_any(path: str, globs: tuple[str, ...] | list[str]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(g) for g in globs)


@dataclass(frozen=True)
class CommandDecision:
    allowed: bool
    reason: str
    argv: tuple[str, ...]


class PolicyEngine:
    def __init__(self, profile: str = "default", platform_text: str | None = None) -> None:
        platform = load_platform_policy(platform_text)
        profiles = platform.get("profiles", {})
        if profile not in profiles:
            raise PolicyError(f"unknown policy profile: {profile!r}")
        base = {k: v for k, v in platform.items() if k != "profiles"}
        self.profile = profile
        self.effective: dict[str, Any] = _deep_merge(base, profiles[profile] or {})
        self.version: str = str(platform["policy_version"])

    # -- snapshot -------------------------------------------------------------------------

    def snapshot(self) -> PolicySnapshot:
        return PolicySnapshot(
            policy_version=self.version,
            profile=self.profile,
            effective=self.effective,
            sha256=content_hash({"profile": self.profile, "effective": self.effective}),
        )

    # -- gates ----------------------------------------------------------------------------

    @property
    def mandatory_gates(self) -> tuple[str, ...]:
        return tuple(self.effective["mandatory_gates"])

    def not_applicable_allowed(self, gate: str) -> bool:
        return gate in self.effective.get("not_applicable_allowed", [])

    @property
    def min_valid_magi_results(self) -> int:
        return int(self.effective["decision"]["min_valid_magi_results"])

    # -- commands -------------------------------------------------------------------------

    def check_command(self, command: str | list[str]) -> CommandDecision:
        try:
            argv = tuple(shlex.split(command) if isinstance(command, str) else command)
        except ValueError as exc:  # e.g. unbalanced quotes: denied, never a crash
            return CommandDecision(False, f"command cannot be parsed: {exc}", ())
        if not argv:
            return CommandDecision(False, "empty command", argv)
        joined = " ".join(argv)
        for denied in self.effective.get("command_denylist", []):
            if f" {denied} " in f" {joined} ":
                return CommandDecision(False, f"denied pattern: {denied!r}", argv)
        exe = PurePosixPath(argv[0]).name
        if "/" in argv[0]:
            return CommandDecision(False, "executables must be referenced by name", argv)
        if exe not in [str(x) for x in self.effective.get("command_allowlist", [])]:
            return CommandDecision(False, f"executable not in allowlist: {exe!r}", argv)
        return CommandDecision(True, "allowed", argv)

    # -- writes ---------------------------------------------------------------------------

    @property
    def protected_paths(self) -> tuple[str, ...]:
        return tuple(self.effective.get("protected_paths", []))

    def check_write(
        self,
        role: AgentId,
        path: str,
        *,
        task_scopes: tuple[str, ...],
        doc_scopes: tuple[str, ...],
        test_scopes: tuple[str, ...] = (),
        delete: bool = False,
    ) -> str:
        """Return the normalized path if `role` may write it, else raise PolicyError."""
        rel = normalize_rel_path(path)
        if role in READ_ONLY_ROLES or role not in CODE_WRITER_ROLES | DOC_WRITER_ROLES:
            raise PolicyError(f"{role.value} has read-only access")
        if matches_any(rel, self.protected_paths):
            raise PolicyError(f"protected path: {rel}")
        if role in DOC_WRITER_ROLES:
            if not matches_any(rel, doc_scopes):
                raise PolicyError(f"{rel} is outside documentation scopes")
            return rel
        if delete and matches_any(rel, test_scopes):
            raise PolicyError(f"deleting tests is not allowed: {rel}")
        if not matches_any(rel, task_scopes):
            raise PolicyError(f"{rel} is outside the task write scope")
        return rel

    # -- execution ------------------------------------------------------------------------

    def runner_allowed(self, runner: str) -> bool:
        return runner in self.effective["execution"]["allowed_runners"]

    def publication_allowed(self, mode: str) -> bool:
        return mode in self.effective["publication"]["allowed_modes"]
