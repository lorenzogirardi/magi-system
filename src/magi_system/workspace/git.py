"""Minimal Git wrapper. Runs `git` with a scrubbed environment and no user config.

Credentials are left to the infrastructure layer (credential helpers / SSH agent of the
caller for *acquisition* only). Nothing returned here contains them: stderr is redacted.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from magi_system.observability.redaction import Redactor

PLATFORM_IDENTITY = ("MAGI System", "magi@localhost")


class GitError(Exception):
    pass


@dataclass(frozen=True)
class GitResult:
    stdout: str
    stderr: str
    returncode: int


def _env(*, allow_credentials: bool) -> dict[str, str]:
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}
    if allow_credentials:
        keep |= {"SSH_AUTH_SOCK", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME=PLATFORM_IDENTITY[0],
        GIT_AUTHOR_EMAIL=PLATFORM_IDENTITY[1],
        GIT_COMMITTER_NAME=PLATFORM_IDENTITY[0],
        GIT_COMMITTER_EMAIL=PLATFORM_IDENTITY[1],
    )
    if not allow_credentials:
        env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def git(
    *args: str,
    cwd: Path | None = None,
    check: bool = True,
    allow_credentials: bool = False,
    timeout: float = 300,
    input_text: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> GitResult:
    env = _env(allow_credentials=allow_credentials)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    redactor = Redactor(include_env=False)
    result = GitResult(proc.stdout, redactor.text(proc.stderr), proc.returncode)
    if check and proc.returncode != 0:
        raise GitError(f"git {args[0]} failed ({proc.returncode}): {result.stderr.strip()[:2000]}")
    return result
