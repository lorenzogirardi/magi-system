"""`.magi/config.yaml` schema, loader and discovery fallback (brief §0.3).

Project configuration is descriptive input, never a source of authorization. The schema
has no fields for gates, permissions, secrets, network or publication; unknown keys are
rejected, so an attempt to set them fails validation and is recorded as a denial.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from magi_system.policy.engine import PolicyEngine, PolicyError, normalize_rel_path

CONFIG_PATH = ".magi/config.yaml"

# Keys that indicate an attempt to change authorization. Reported explicitly.
AUTHORIZATION_KEYS = frozenset(
    {
        "gates",
        "mandatory_gates",
        "disable_gates",
        "skip_gates",
        "permissions",
        "secrets",
        "env_secrets",
        "network",
        "allow_network",
        "push",
        "merge",
        "deploy",
        "publication",
        "policy",
        "profile",
        "command_allowlist",
        "thresholds",
    }
)


class ProjectCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: str = Field(min_length=1)
    timeout_seconds: float | None = Field(default=None, gt=0)


class ProjectService(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    command: str
    ready_command: str = ""


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    languages: tuple[str, ...] = ()
    code_paths: tuple[str, ...] = ("**",)
    test_paths: tuple[str, ...] = ()
    doc_paths: tuple[str, ...] = ("docs/**", "README.md", "CHANGELOG.md")
    generated_paths: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()  # narrower scopes requested by the project
    commands: dict[str, ProjectCommand] = {}  # keys: build, test, lint
    doc_conventions: str = ""
    container_image: str = ""
    services: tuple[ProjectService, ...] = ()
    # Justifications for NOT_APPLICABLE gates. Only honoured for gates the platform policy
    # lists in `not_applicable_allowed`; anything else is ignored and the gate still runs.
    not_applicable: dict[str, str] = {}

    @field_validator("code_paths", "test_paths", "doc_paths", "generated_paths", "write_scopes")
    @classmethod
    def _rel_globs(cls, globs: tuple[str, ...]) -> tuple[str, ...]:
        for g in globs:
            if g.startswith("/") or ".." in g.split("/"):
                raise ValueError(f"glob must be repo-relative: {g!r}")
        return globs

    @field_validator("commands")
    @classmethod
    def _known_commands(cls, cmds: dict[str, ProjectCommand]) -> dict[str, ProjectCommand]:
        unknown = set(cmds) - {"build", "test", "lint"}
        if unknown:
            raise ValueError(f"unknown command keys: {sorted(unknown)}")
        return cmds


class ConfigSource(StrEnum):
    FILE = "file"
    DISCOVERY = "discovery"


class ResolvedProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: ConfigSource
    config: ProjectConfig
    inferred: tuple[str, ...] = ()  # notes on what discovery inferred
    warnings: tuple[str, ...] = ()
    denied_commands: dict[str, str] = {}  # command key -> policy reason


class ProjectConfigError(Exception):
    def __init__(self, message: str, *, authorization_attempt: bool = False) -> None:
        super().__init__(message)
        self.authorization_attempt = authorization_attempt


def parse_project_config(text: str) -> ProjectConfig:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProjectConfigError(f"{CONFIG_PATH} is not valid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ProjectConfigError(f"{CONFIG_PATH} must be a mapping")
    attempted = sorted(set(raw) & AUTHORIZATION_KEYS)
    if attempted:
        raise ProjectConfigError(
            f"{CONFIG_PATH} attempts to set authorization keys {attempted}; "
            "project configuration cannot change gates, permissions, secrets, network "
            "or publication",
            authorization_attempt=True,
        )
    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise ProjectConfigError(f"{CONFIG_PATH} failed validation: {exc}") from exc


def discover_project(root: Path) -> tuple[ProjectConfig, tuple[str, ...]]:
    """Infer a conservative config from manifests. Never invents commands it can't justify."""
    notes: list[str] = []
    languages: list[str] = []
    commands: dict[str, ProjectCommand] = {}
    tests: list[str] = []

    makefile = root / "Makefile"
    if makefile.is_file():
        text = makefile.read_text(errors="replace")
        for target in ("build", "test", "lint"):
            if f"\n{target}:" in f"\n{text}":
                commands[target] = ProjectCommand(run=f"make {target}")
                notes.append(f"{target}: 'make {target}' (Makefile target found)")
    if (root / "package.json").is_file():
        languages.append("javascript")
        import json

        try:
            scripts = json.loads((root / "package.json").read_text()).get("scripts", {})
        except ValueError:
            scripts = {}
            notes.append("package.json unreadable; no npm scripts inferred")
        for target in ("build", "test", "lint"):
            if target in scripts and target not in commands:
                commands[target] = ProjectCommand(run=f"npm run {target}")
                notes.append(f"{target}: 'npm run {target}' (package.json script)")
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file():
        languages.append("python")
        if (root / "tests").is_dir() and "test" not in commands:
            commands["test"] = ProjectCommand(run="python -m pytest -q")
            notes.append("test: 'python -m pytest -q' (python project with tests/)")
    if any(root.glob("*.sh")) or makefile.is_file():
        languages.append("shell")
    for candidate in ("tests", "test", "spec"):
        if (root / candidate).is_dir():
            tests.append(f"{candidate}/**")
    for target in ("build", "test", "lint"):
        if target not in commands:
            notes.append(f"{target}: no command discovered; gate will be UNKNOWN unless N/A allowed")
    return (
        ProjectConfig(languages=tuple(languages), test_paths=tuple(tests), commands=commands),
        tuple(notes),
    )


def resolve_project_config(root: Path, policy: PolicyEngine) -> ResolvedProjectConfig:
    """Load `.magi/config.yaml` or discover. Authorization attempts always raise.

    A malformed (non-authorization) file follows `project_config.on_invalid` in the
    platform policy: `deny` raises, `discover` falls back with a warning.
    """
    path = root / CONFIG_PATH
    warnings: list[str] = []
    if path.is_file():
        try:
            config = parse_project_config(path.read_text(errors="replace"))
            source, inferred = ConfigSource.FILE, ()
            # Commands the file does not declare are discovered, and recorded as inferred.
            discovered, notes = discover_project(root)
            missing = {k: v for k, v in discovered.commands.items() if k not in config.commands}
            if missing:
                config = config.model_copy(update={"commands": {**config.commands, **missing}})
                inferred = tuple(n for n in notes if n.split(":", 1)[0] in missing)
        except ProjectConfigError as exc:
            if exc.authorization_attempt or policy.effective["project_config"]["on_invalid"] == "deny":
                raise
            warnings.append(f"invalid project config ignored: {exc}")
            config, inferred = discover_project(root)
            source = ConfigSource.DISCOVERY
    else:
        config, inferred = discover_project(root)
        source = ConfigSource.DISCOVERY

    denied: dict[str, str] = {}
    for key, cmd in config.commands.items():
        decision = policy.check_command(cmd.run)
        if not decision.allowed:
            denied[key] = decision.reason
    for scope in config.write_scopes:
        try:
            normalize_rel_path(scope.replace("*", "x"))
        except PolicyError as exc:
            raise ProjectConfigError(str(exc)) from exc
    return ResolvedProjectConfig(
        source=source,
        config=config,
        inferred=inferred,
        warnings=tuple(warnings),
        denied_commands=denied,
    )
