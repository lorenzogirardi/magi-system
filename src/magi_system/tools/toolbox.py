"""Role-scoped tools over one run workspace (brief §11).

Access boundaries are enforced here, in code, not by prompts:
- every path is normalized and must resolve inside the run workspace (symlinks included);
- writes go through `PolicyEngine.check_write` (role, task scopes, doc scopes, protected paths);
- commands go through the `ExecutionRunner`, which applies the command policy;
- read-only roles get no write/delete/command tools at all.

Tool outputs are data. They are returned wrapped so prompts can mark them untrusted.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magi_system.execution.runner import ExecutionRunner
from magi_system.models.common import AgentId
from magi_system.policy.engine import (
    CODE_WRITER_ROLES,
    DOC_WRITER_ROLES,
    PolicyEngine,
    PolicyError,
    matches_any,
    normalize_rel_path,
)

MAX_READ_BYTES = 200_000
COMMAND_ROLES = CODE_WRITER_ROLES | {AgentId.QA, AgentId.SRE, AgentId.SECURITY, AgentId.VERIFIER}


class ToolError(Exception):
    pass


@dataclass
class Toolbox:
    role: AgentId
    workspace: Path
    policy: PolicyEngine
    runner: ExecutionRunner | None = None
    task_scopes: tuple[str, ...] = ()
    doc_scopes: tuple[str, ...] = ()
    test_scopes: tuple[str, ...] = ()
    required_scopes: tuple[str, ...] = ()  # project-requested narrowing: path must ALSO match
    on_call: Callable[[str], None] | None = None  # budget hook: raises when exhausted

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()

    # -- registry -------------------------------------------------------------------------

    def available(self) -> dict[str, str]:
        tools = {
            "list_files": "List repository files. args: {prefix?: str}",
            "read_file": "Read a text file. args: {path: str}",
            "grep": "Search a regex in files. args: {pattern: str, glob?: str}",
        }
        if self.role in CODE_WRITER_ROLES | DOC_WRITER_ROLES:
            tools["write_file"] = "Create or overwrite a file. args: {path: str, content: str}"
            tools["delete_file"] = "Delete a file. args: {path: str}"
        if self.role in COMMAND_ROLES and self.runner is not None:
            tools["run_command"] = "Run an allowlisted command in the workspace. args: {command: str}"
        return tools

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool not in self.available():
            raise ToolError(f"tool {tool!r} is not available to {self.role.value}")
        if self.on_call:
            self.on_call(tool)
        try:
            result = getattr(self, f"_t_{tool}")(**args)
        except TypeError as exc:
            raise ToolError(f"bad arguments for {tool}: {exc}") from exc
        except (PolicyError, ToolError) as exc:
            raise ToolError(str(exc)) from exc
        return result

    # -- path handling --------------------------------------------------------------------

    def resolve(self, path: str) -> tuple[str, Path]:
        rel = normalize_rel_path(path)
        full = (self.workspace / rel).resolve()
        try:
            full.relative_to(self.workspace)
        except ValueError as exc:
            raise ToolError(f"path escapes the workspace: {path}") from exc
        if rel == ".git" or rel.startswith(".git/"):
            raise ToolError("the .git directory is not accessible to agents")
        return rel, full

    # -- read tools -----------------------------------------------------------------------

    def _iter_files(self):
        for p in sorted(self.workspace.rglob("*")):
            if ".git" in p.relative_to(self.workspace).parts:
                continue
            if p.is_file():
                yield str(p.relative_to(self.workspace)), p

    def _t_list_files(self, prefix: str = "") -> dict[str, Any]:
        files = [rel for rel, _ in self._iter_files() if rel.startswith(prefix)]
        return {"files": files[:2000], "summary": f"{len(files)} files"}

    def _t_read_file(self, path: str) -> dict[str, Any]:
        rel, full = self.resolve(path)
        if not full.is_file():
            raise ToolError(f"file not found: {rel}")
        data = full.read_bytes()[:MAX_READ_BYTES]
        return {"path": rel, "content": data.decode(errors="replace"), "summary": f"read {rel}"}

    def _t_grep(self, pattern: str, glob: str = "**") -> dict[str, Any]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from exc
        hits = []
        for rel, p in self._iter_files():
            if not matches_any(rel, (glob,)):
                continue
            try:
                lines = p.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    hits.append({"path": rel, "line": i, "text": line[:300]})
                    if len(hits) >= 500:
                        break
        return {"matches": hits, "summary": f"{len(hits)} matches"}

    # -- write tools ----------------------------------------------------------------------

    def _check_write(self, path: str, *, delete: bool = False) -> tuple[str, Path]:
        rel, full = self.resolve(path)
        self.policy.check_write(
            self.role, rel, task_scopes=self.task_scopes, doc_scopes=self.doc_scopes,
            test_scopes=self.test_scopes, delete=delete,
        )
        if self.required_scopes and not matches_any(rel, self.required_scopes):
            raise PolicyError(f"{rel} is outside the project write scopes")
        return rel, full

    def _t_write_file(self, path: str, content: str) -> dict[str, Any]:
        rel, full = self._check_write(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return {"path": rel, "summary": f"wrote {rel} ({len(content)} chars)"}

    def _t_delete_file(self, path: str) -> dict[str, Any]:
        rel, full = self._check_write(path, delete=True)
        if not full.is_file():
            raise ToolError(f"file not found: {rel}")
        full.unlink()
        return {"path": rel, "summary": f"deleted {rel}"}

    # -- commands -------------------------------------------------------------------------

    def _t_run_command(self, command: str) -> dict[str, Any]:
        assert self.runner is not None
        r = self.runner.run(command, cwd=self.workspace)
        if r.denied:
            raise ToolError(f"command denied by policy: {r.denial_reason}")
        return {
            "command": command,
            "exit_code": r.exit_code,
            "timed_out": r.timed_out,
            "stdout": r.stdout[-20000:],
            "stderr": r.stderr[-20000:],
            "summary": f"exit={r.exit_code} timed_out={r.timed_out}",
        }
