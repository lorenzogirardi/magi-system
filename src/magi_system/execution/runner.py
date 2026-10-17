"""ExecutionRunner: runs target commands with limits (brief §0.4, §11).

Every command is first checked against the platform policy. Project-defined commands are
untrusted input and go through the same path.

- `ContainerRunner` (default): podman/docker, `--network=none`, only the run workspace is
  mounted, no credentials, no container socket, memory/pids limits, dropped capabilities.
- `LocalRunner`: executes on the host with a scrubbed environment and a timeout.
  It is for development and offline tests only and is NOT a sandbox: the command runs
  with the caller's user privileges and can read anything that user can read.
- `CodeInterpreterRunner` (AgentCore hosting, `agentcore` profile): runs each command in an
  Amazon Bedrock AgentCore Code Interpreter session that mounts only this run's workspace
  through a per-run EFS access point. See docs/agentcore/security.md.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from magi_system.observability.redaction import Redactor
from magi_system.policy.engine import PolicyEngine
from magi_system.workspace.manager import TOOLCHAIN_DIR


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

    def available(self) -> bool: ...

    def unavailable_hint(self) -> str: ...

    def prepare_toolchain(self, requirements: list[str], packages: list[str] | None = None) -> None: ...

    def close(self) -> None: ...


class ToolchainError(RuntimeError):
    """The sandbox toolchain could not be prepared; reported before any agent runs."""


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

    def available(self) -> bool:
        return True

    def unavailable_hint(self) -> str:
        return ""

    def prepare_toolchain(self, requirements: list[str], packages: list[str] | None = None) -> None:
        """Make the target's Python dependencies available to commands (codeinterpreter only)."""
        if requirements or packages:
            raise ToolchainError("sandbox.requirements and sandbox.packages are supported only by the codeinterpreter runner")

    def close(self) -> None:
        """Release run-scoped resources (sandbox sessions ...). Safe to call more than once."""


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

    def unavailable_hint(self) -> str:
        return (f"container image {self.image} is not present locally (or no container runtime found); "
                f"pull it first: podman pull {self.image}  (or: docker pull {self.image})")

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


class CodeInterpreterRunner(_BaseRunner):
    """Runs commands in an AgentCore Code Interpreter session (managed sandbox).

    Isolation: the session mounts a per-run EFS access point rooted at this run's workspace
    (nothing wider), runs in subnets without internet access, and its execution role grants
    only that EFS mount. Each command gets an empty environment (`env -i`) and a timeout.
    Configuration comes only from environment variables (`CONFIG_ENV`), never from the policy.
    """

    name = "codeinterpreter"
    MOUNT = "/mnt/ws"
    # InvokeCodeInterpreter is synchronous and capped at 15 minutes; keep a margin.
    MAX_SYNC_SECONDS = 840.0
    # The sandbox has Python 3.12 (aarch64, no internet, no git, no pytest) under /opt/amazon/...
    DEFAULT_PATH = "/opt/amazon/genesis1p-tools/venv/bin:/usr/local/bin:/usr/bin:/bin"
    SANDBOX_PLATFORM, SANDBOX_PYTHON = "aarch64-manylinux_2_28", "3.12"
    TOOLCHAIN_DIR = TOOLCHAIN_DIR  # inside .git of the run workspace: mounted by the sandbox, invisible to tools and patches
    CONFIG_ENV = {
        "identifier": "MAGI_CI_IDENTIFIER",
        "file_system_id": "MAGI_EFS_FILE_SYSTEM_ID",
        "file_system_arn": "MAGI_EFS_FILE_SYSTEM_ARN",
        "runs_path": "MAGI_EFS_RUNS_PATH",  # EFS path that holds run directories (runs_root on EFS)
    }

    def __init__(self, policy: PolicyEngine, workspace_root: Path, *, env: dict[str, str] | None = None,
                 clients: tuple | None = None) -> None:
        super().__init__(policy, workspace_root)
        env = os.environ if env is None else env
        self.cfg = {k: env.get(v, "") for k, v in self.CONFIG_ENV.items()}
        self.uid = int(env.get("MAGI_EFS_POSIX_UID", "1000"))
        self.gid = int(env.get("MAGI_EFS_POSIX_GID", "1000"))
        self.toolchain_path = env.get("MAGI_CI_TOOLCHAIN_PATH", self.DEFAULT_PATH)
        self.session_timeout = int(env.get("MAGI_CI_SESSION_TIMEOUT_SECONDS", "28800"))
        self.run_id = self.workspace_root.parent.name
        self._clients = clients
        self._access_point: dict | None = None
        self._session_id = ""

    def environment_id(self) -> str:
        return f"codeinterpreter:{self.cfg['identifier'] or 'unconfigured'}"

    def missing_config(self) -> list[str]:
        return [self.CONFIG_ENV[k] for k, v in self.cfg.items() if not v]

    def available(self) -> bool:
        return not self.missing_config()

    def unavailable_hint(self) -> str:
        return f"Code Interpreter runner is not configured; missing environment variables: {', '.join(self.missing_config())}"

    # -- AWS resources --------------------------------------------------------------------

    def _aws(self):
        if self._clients is None:
            import boto3

            self._clients = (boto3.client("bedrock-agentcore"), boto3.client("efs"))
        return self._clients

    @property
    def efs_root(self) -> str:
        return f"{self.cfg['runs_path'].rstrip('/')}/{self.run_id}/workspace"

    def _ensure_access_point(self) -> dict:
        if self._access_point:
            return self._access_point
        _, efs = self._aws()
        tag = {"Key": "magi-run", "Value": self.run_id}
        found, token = [], None
        while True:  # an access point left by an interrupted process is reused on resume
            page = efs.describe_access_points(FileSystemId=self.cfg["file_system_id"], **({"NextToken": token} if token else {}))
            found += [ap for ap in page.get("AccessPoints", [])
                      if tag in ap.get("Tags", []) and ap.get("RootDirectory", {}).get("Path") == self.efs_root]
            token = page.get("NextToken")
            if not token:
                break
        ap = found[0] if found else efs.create_access_point(
            FileSystemId=self.cfg["file_system_id"],
            PosixUser={"Uid": self.uid, "Gid": self.gid},
            RootDirectory={"Path": self.efs_root,
                           "CreationInfo": {"OwnerUid": self.uid, "OwnerGid": self.gid, "Permissions": "0755"}},
            Tags=[tag, {"Key": "Name", "Value": f"magi-{self.run_id}"}],
        )
        deadline = time.monotonic() + 60
        while ap.get("LifeCycleState", "available") != "available":
            if time.monotonic() > deadline:
                raise OSError(f"EFS access point {ap['AccessPointId']} not available after 60 s")
            time.sleep(1)
            ap = efs.describe_access_points(AccessPointId=ap["AccessPointId"])["AccessPoints"][0]
        self._access_point = ap
        return ap

    def _ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
        ap = self._ensure_access_point()
        agentcore, _ = self._aws()
        resp = agentcore.start_code_interpreter_session(
            codeInterpreterIdentifier=self.cfg["identifier"],
            name=f"magi-{self.run_id}"[:100],
            sessionTimeoutSeconds=self.session_timeout,
            filesystemConfigurations=[{"efsConfiguration": {
                "accessPointArn": ap["AccessPointArn"], "fileSystemArn": self.cfg["file_system_arn"], "mountPath": self.MOUNT}}],
        )
        self._session_id = resp["sessionId"]
        return self._session_id

    # -- toolchain ------------------------------------------------------------------------

    @staticmethod
    def check_requirements(name: str, text: str) -> None:
        """Requirements come from the target repository (untrusted): plain pinned names only."""
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if line and (line.startswith("-") or "://" in line or "@" in line or line.startswith((".", "/", "~"))):
                raise ToolchainError(f"{name}: unsupported requirement line {line!r} (only plain package specifiers are installed)")

    def prepare_toolchain(self, requirements: list[str], packages: list[str] | None = None) -> None:
        """Install the target's wheels (plus `packages`, e.g. a linter) into the run workspace so the
        sandbox, which has no internet, can import them. Runs here (the runtime has egress), wheels
        only: nothing executes at install. Idempotent: skipped when nothing changed."""
        packages = packages or []
        if not requirements and not packages:
            return
        self.check_requirements("sandbox.packages", "\n".join(packages))
        root = self.workspace_root.resolve()
        files = []
        for rel in requirements:
            f = (root / rel).resolve()
            if not f.is_relative_to(root) or not f.is_file():
                raise ToolchainError(f"sandbox.requirements: {rel!r} is not a file inside the repository")
            self.check_requirements(rel, f.read_text())
            files.append(f)
        base = root / self.TOOLCHAIN_DIR
        site, marker = base / "site", base / "requirements.sha256"
        digest = hashlib.sha256(b"\0".join([self.SANDBOX_PLATFORM.encode(), self.SANDBOX_PYTHON.encode(),
                                            *(f.read_bytes() for f in files),
                                            *(p.encode() for p in packages)])).hexdigest()
        if marker.is_file() and marker.read_text() == digest and site.is_dir():
            return
        shutil.rmtree(base, ignore_errors=True)
        site.mkdir(parents=True)
        cmd = ["uv", "pip", "install", "--python", sys.executable, "--target", str(site),
               "--python-platform", self.SANDBOX_PLATFORM, "--python-version", self.SANDBOX_PYTHON,
               "--only-binary", ":all:", *[a for f in files for a in ("-r", str(f))], "pytest", *packages]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                                  env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolchainError(f"installing sandbox requirements failed: {type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            raise ToolchainError(f"installing sandbox requirements failed (exit {proc.returncode}): "
                                 f"{(proc.stderr or proc.stdout).strip()[-1500:]}")
        marker.write_text(digest)

    # -- execution ------------------------------------------------------------------------

    def remote_command(self, argv: tuple[str, ...], cwd: Path, timeout: float) -> str:
        rel = cwd.resolve().relative_to(self.workspace_root)
        remote_cwd = self.MOUNT if str(rel) == "." else f"{self.MOUNT}/{rel.as_posix()}"
        env = (f"PATH={self.toolchain_path} PYTHONPATH={self.MOUNT}/{self.TOOLCHAIN_DIR}/site "
               f"HOME=/tmp LANG=C.UTF-8 CI=1 MAGI_RUNNER={self.name}")
        return (f"cd {shlex.quote(remote_cwd)} && exec env -i {env} "
                f"timeout -k 5 {int(timeout)} {shlex.join(argv)} < /dev/null")  # stdin closed: `python -` would wait for the timeout

    @staticmethod
    def parse(resp: dict) -> tuple[int | None, str, str]:
        """(exit code, stdout, stderr) from an executeCommand response; exit None if absent."""
        out, err, code = [], [], None
        for event in resp.get("stream", []):
            if "result" not in event:
                problem = next(iter(event), "unknown event")
                err.append(f"codeinterpreter: {problem}: {event.get(problem, '')}")
                continue
            sc = event["result"].get("structuredContent") or {}
            out.append(sc.get("stdout") or "")
            err.append(sc.get("stderr") or "")
            if sc.get("exitCode") is not None:
                code = int(sc["exitCode"])
        if code is None:
            err.append("codeinterpreter: response carried no exit code")
        return code, "".join(out), "\n".join(e for e in err if e)

    def run(self, command, *, cwd: Path, timeout: float | None = None) -> CommandResult:
        argv, denied = self._check(command, cwd)
        if denied:
            return denied
        started = time.monotonic()
        limit = min(timeout or self.default_timeout, self.MAX_SYNC_SECONDS)
        script = self.remote_command(argv, cwd, limit)
        agentcore, _ = self._aws()
        resp = None
        for attempt in (1, 2):  # a second attempt with a fresh session if the old one expired
            try:
                resp = agentcore.invoke_code_interpreter(
                    codeInterpreterIdentifier=self.cfg["identifier"], sessionId=self._ensure_session(),
                    name="executeCommand", arguments={"command": script},
                )
                break
            except Exception as exc:  # boto ClientError, connection errors
                self._session_id = ""
                if attempt == 2:
                    return self._finish(argv, None, "", f"codeinterpreter invocation failed: {type(exc).__name__}: {exc}", started)
        code, out, err = self.parse(resp)
        # coreutils `timeout` exits 124 (137 after -k) when it had to stop the command.
        return self._finish(argv, code, out, err, started, timed_out=code in (124, 137) and time.monotonic() - started >= limit)

    def close(self) -> None:
        if self._clients is None:
            return
        agentcore, efs = self._clients
        if self._session_id:
            try:
                agentcore.stop_code_interpreter_session(codeInterpreterIdentifier=self.cfg["identifier"], sessionId=self._session_id)
            except Exception:
                pass  # the session times out by itself
            self._session_id = ""
        if self._access_point:
            efs.delete_access_point(AccessPointId=self._access_point["AccessPointId"])
            self._access_point = None


def make_runner(policy: PolicyEngine, workspace_root: Path, *, runner: str | None = None, image: str = "") -> ExecutionRunner:
    choice = runner or policy.effective["execution"]["default_runner"]
    if not policy.runner_allowed(choice):
        raise PermissionError(f"runner {choice!r} not allowed by policy profile {policy.profile!r}")
    if choice == "local":
        return LocalRunner(policy, workspace_root)
    if choice == "container":
        return ContainerRunner(policy, workspace_root, image or policy.effective["execution"]["container"]["default_images"]["shell"])
    if choice == "codeinterpreter":
        return CodeInterpreterRunner(policy, workspace_root)
    raise ValueError(f"unknown runner {choice!r}")
