"""ExecutionRunner: runs target commands with limits (brief §0.4, §11).

Every command is first checked against the platform policy. Project-defined commands are
untrusted input and go through the same path.

- `ContainerRunner` (default): podman/docker, `--network=none`, only the run workspace is
  mounted, no credentials, no container socket, memory/pids limits, dropped capabilities.
- `LocalRunner`: executes on the host with a scrubbed environment and a timeout.
  It is for development and offline tests only and is NOT a sandbox: the command runs
  with the caller's user privileges and can read anything that user can read.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from magi_system.observability.redaction import Redactor
from magi_system.policy.engine import PolicyEngine


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int | None  # None when not executed / timed out
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    denied: bool = False
    denial_reason: str = ""
    runner: str = ""
    environment_id: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.denied


class ExecutionRunner(Protocol):
    name: str

    def environment_id(self) -> str: ...

    def run(self, command: str | list[str], *, cwd: Path, timeout: float | None = None) -> CommandResult: ...


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[truncated {len(text) - limit} chars]"


class _BaseRunner:
    name = "base"

    def __init__(self, policy: PolicyEngine, workspace_root: Path) -> None:
        self.policy = policy
        self.workspace_root = workspace_root.resolve()
        self.redactor = Redactor()
        limits = policy.effective["limits"]
        self.default_timeout = float(limits["command_timeout_seconds"])
        self.max_output = int(limits["max_output_bytes"])

    def _check(self, command: str | list[str], cwd: Path) -> tuple[tuple[str, ...], CommandResult | None]:
        decision = self.policy.check_command(command)
        if not decision.allowed:
            return decision.argv, CommandResult(
                decision.argv, None, "", "", 0.0, denied=True, denial_reason=decision.reason, runner=self.name
            )
        try:
            cwd.resolve().relative_to(self.workspace_root)
        except ValueError:
            return decision.argv, CommandResult(
                decision.argv, None, "", "", 0.0, denied=True,
                denial_reason="working directory outside the run workspace", runner=self.name,
            )
        return decision.argv, None

    def _finish(self, argv, code, out, err, started, timed_out=False) -> CommandResult:
        return CommandResult(
            argv=argv,
            exit_code=None if timed_out else code,
            stdout=_truncate(self.redactor.text(out or ""), self.max_output),
            stderr=_truncate(self.redactor.text(err or ""), self.max_output),
            duration_seconds=round(time.monotonic() - started, 3),
            timed_out=timed_out,
            runner=self.name,
            environment_id=self.environment_id(),
        )

    def environment_id(self) -> str:  # pragma: no cover - overridden
        return self.name


class LocalRunner(_BaseRunner):
    """Development runner. NOT a sandbox — see module docstring."""

    name = "local"

    def environment_id(self) -> str:
        return f"local:{os.uname().sysname}-{os.uname().machine}"

    def _env(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.workspace_root),
            "LANG": "C.UTF-8",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "CI": "1",
            "MAGI_RUNNER": "local",
        }

    def run(self, command, *, cwd: Path, timeout: float | None = None) -> CommandResult:
        argv, denied = self._check(command, cwd)
        if denied:
            return denied
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                list(argv), cwd=cwd, env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, text=True, start_new_session=True,
            )
        except FileNotFoundError as exc:
            return self._finish(argv, 127, "", str(exc), started)
        try:
            out, err = proc.communicate(timeout=timeout or self.default_timeout)
            return self._finish(argv, proc.returncode, out, err, started)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            return self._finish(argv, None, out, err, started, timed_out=True)


class ContainerRunner(_BaseRunner):
    """Runs commands in a throwaway container with only the run workspace mounted."""

    name = "container"

    def __init__(self, policy: PolicyEngine, workspace_root: Path, image: str, runtime: str | None = None) -> None:
        super().__init__(policy, workspace_root)
        cfg = policy.effective["execution"]["container"]
        if not any(image.startswith(p) for p in cfg["image_allowlist_prefixes"]):
            raise ValueError(f"container image not allowed by policy: {image}")
        self.image = image
        self.runtime = runtime or shutil.which("podman") or shutil.which("docker") or ""
        self.cfg = cfg

    def environment_id(self) -> str:
        return f"container:{Path(self.runtime).name or 'none'}:{self.image}"

    # Variables the runtime CLI itself needs (rootless podman, remote engines). They are given
    # to the CLI process only; the container receives nothing but the explicit --env flags.
    CLI_ENV = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DOCKER_HOST", "CONTAINER_HOST", "DOCKER_CONFIG")

    def cli_env(self) -> dict[str, str]:
        return {k: os.environ[k] for k in self.CLI_ENV if k in os.environ}

    def available(self) -> bool:
        """True when the runtime exists and the image is present locally (never pulls)."""
        if not self.runtime:
            return False
        r = subprocess.run([self.runtime, "image", "inspect", self.image], capture_output=True, env=self.cli_env())
        return r.returncode == 0

    def build_argv(self, argv: tuple[str, ...], cwd: Path) -> list[str]:
        rel = cwd.resolve().relative_to(self.workspace_root)
        # Docker runs as root by default: files it writes into the bind mount would become
        # root-owned and break later git operations. Rootless podman already maps the user.
        user = [f"--user={os.getuid()}:{os.getgid()}"] if Path(self.runtime).name == "docker" else []
        return [
            self.runtime, "run", "--rm", "--pull=never", *user,
            f"--network={self.cfg['network']}",
            f"--memory={self.cfg['memory']}", f"--pids-limit={self.cfg['pids_limit']}",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs=/tmp:rw,size=256m", "--env=HOME=/tmp", "--env=CI=1",
            f"--volume={self.workspace_root}:/workspace:rw",
            f"--workdir=/workspace/{rel}" if str(rel) != "." else "--workdir=/workspace",
            self.image, *argv,
        ]

    def run(self, command, *, cwd: Path, timeout: float | None = None) -> CommandResult:
        argv, denied = self._check(command, cwd)
        if denied:
            return denied
        started = time.monotonic()
        if not self.runtime:
            return self._finish(argv, 125, "", "no container runtime available", started)
        try:
            proc = subprocess.run(
                self.build_argv(argv, cwd), capture_output=True, text=True,
                timeout=timeout or self.default_timeout, env=self.cli_env(),
            )
            return self._finish(argv, proc.returncode, proc.stdout, proc.stderr, started)
        except subprocess.TimeoutExpired as exc:
            return self._finish(argv, None, exc.stdout or "", exc.stderr or "", started, timed_out=True)


def make_runner(policy: PolicyEngine, workspace_root: Path, *, runner: str | None = None, image: str = "") -> ExecutionRunner:
    choice = runner or policy.effective["execution"]["default_runner"]
    if not policy.runner_allowed(choice):
        raise PermissionError(f"runner {choice!r} not allowed by policy profile {policy.profile!r}")
    if choice == "local":
        return LocalRunner(policy, workspace_root)
    if choice == "container":
        return ContainerRunner(policy, workspace_root, image or policy.effective["execution"]["container"]["default_images"]["shell"])
    raise ValueError(f"unknown runner {choice!r}")
