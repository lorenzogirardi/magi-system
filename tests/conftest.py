"""Shared fixtures. Target repositories are always created under pytest's tmp_path,
outside the magi-system control repository."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from magi_system.offline import DEMO_FILES
from magi_system.workspace import WorkspaceManager, control_repo_root, git

CONTROL_ROOT = control_repo_root()

FIXTURE_FILES = DEMO_FILES


def make_repo(root: Path, files: dict[str, str] | None = None, message: str = "initial") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", "-b", "main", cwd=root)
    for rel, text in (files or FIXTURE_FILES).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git("add", "-A", cwd=root)
    git("commit", "--quiet", "-m", message, cwd=root)
    return root


def tree_digest(root: Path) -> str:
    """Hash of every file (including .git) to prove a checkout was not modified."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in {".lock"}:
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def control_digest() -> str:
    """Hash of tracked-or-untracked source files of the control repo (excluding caches)."""
    h = hashlib.sha256()
    for sub in ("src", "pyproject.toml"):
        base = CONTROL_ROOT / sub
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for p in paths:
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p.relative_to(CONTROL_ROOT)).encode())
                h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "targets" / "greeter")


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "runs"


@pytest.fixture
def workspaces(runs_root: Path) -> WorkspaceManager:
    return WorkspaceManager(runs_root)
