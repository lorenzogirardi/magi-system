"""CodeInterpreterRunner with fake AgentCore/EFS clients (no AWS calls)."""

import pytest

from magi_system.execution import CodeInterpreterRunner, make_runner
from magi_system.execution.runner import ToolchainError
from magi_system.policy import PolicyEngine

ENV = {
    "MAGI_CI_IDENTIFIER": "magi_ci-abc123",
    "MAGI_EFS_FILE_SYSTEM_ID": "fs-0123",
    "MAGI_EFS_FILE_SYSTEM_ARN": "arn:aws:elasticfilesystem:eu-west-1:000000000000:file-system/fs-0123",
    "MAGI_EFS_RUNS_PATH": "/magi",
    "MAGI_CI_TOOLCHAIN_PATH": "/mnt/ws/.toolchain/bin:/usr/bin:/bin",
}


class FakeEfs:
    def __init__(self):
        self.created, self.deleted, self.aps = [], [], []

    def describe_access_points(self, **kw):
        if "AccessPointId" in kw:
            return {"AccessPoints": [a for a in self.aps if a["AccessPointId"] == kw["AccessPointId"]]}
        return {"AccessPoints": list(self.aps)}

    def create_access_point(self, **kw):
        self.created.append(kw)
        ap = {**kw, "AccessPointId": f"fsap-{len(self.created)}", "AccessPointArn": f"arn:ap/{len(self.created)}", "LifeCycleState": "available"}
        self.aps.append(ap)
        return ap

    def delete_access_point(self, AccessPointId):
        self.deleted.append(AccessPointId)
        self.aps = [a for a in self.aps if a["AccessPointId"] != AccessPointId]


class FakeAgentCore:
    def __init__(self, result=None, fail_first=False):
        self.sessions, self.invocations, self.stopped = [], [], []
        self.result = result or {"stdout": "hello\n", "stderr": "", "exitCode": 0}
        self.fail_first = fail_first

    def start_code_interpreter_session(self, **kw):
        self.sessions.append(kw)
        return {"sessionId": f"s-{len(self.sessions)}", "codeInterpreterIdentifier": kw["codeInterpreterIdentifier"]}

    def invoke_code_interpreter(self, **kw):
        self.invocations.append(kw)
        if self.fail_first and len(self.invocations) == 1:
            raise RuntimeError("session expired")
        return {"sessionId": kw["sessionId"], "stream": [{"result": {"content": [], "structuredContent": self.result, "isError": False}}]}

    def stop_code_interpreter_session(self, **kw):
        self.stopped.append(kw["sessionId"])


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "runs" / "run-x" / "workspace"
    (d / "sub").mkdir(parents=True)
    return d


def runner(ws, agentcore=None, efs=None, env=ENV):
    agentcore, efs = agentcore or FakeAgentCore(), efs or FakeEfs()
    return CodeInterpreterRunner(PolicyEngine("agentcore"), ws, env=env, clients=(agentcore, efs)), agentcore, efs


def test_runs_command_in_per_run_sandbox(ws):
    r, ac, efs = runner(ws)
    res = r.run("sh ok.sh", cwd=ws)
    assert res.ok and res.stdout == "hello\n" and res.runner == "codeinterpreter"
    assert res.environment_id == "codeinterpreter:magi_ci-abc123"
    # the access point is rooted at THIS run's workspace and nothing wider
    (ap,) = efs.created
    assert ap["RootDirectory"]["Path"] == "/magi/run-x/workspace"
    assert {"Key": "magi-run", "Value": "run-x"} in ap["Tags"]
    (sess,) = ac.sessions
    (fs,) = sess["filesystemConfigurations"]
    assert fs["efsConfiguration"]["accessPointArn"] == "arn:ap/1" and fs["efsConfiguration"]["mountPath"] == "/mnt/ws"
    cmd = ac.invocations[0]["arguments"]["command"]
    assert ac.invocations[0]["name"] == "executeCommand"
    assert cmd.startswith("cd /mnt/ws && exec env -i PATH=/mnt/ws/.toolchain/bin:/usr/bin:/bin "
                          "PYTHONPATH=/mnt/ws/.git/magi-toolchain/site HOME=/tmp")
    assert cmd.endswith("timeout -k 5 300 sh ok.sh < /dev/null")  # closed stdin: a command reading it must not hang


def test_session_and_access_point_are_reused(ws):
    r, ac, efs = runner(ws)
    r.run("sh a.sh", cwd=ws)
    r.run("sh b.sh", cwd=ws / "sub")
    assert len(ac.sessions) == 1 and len(efs.created) == 1
    assert ac.invocations[1]["arguments"]["command"].startswith("cd /mnt/ws/sub && ")


def test_existing_access_point_for_run_is_reused_on_resume(ws):
    efs = FakeEfs()
    first, _, _ = runner(ws, efs=efs)
    first.run("sh a.sh", cwd=ws)
    second, _, _ = runner(ws, efs=efs)  # a resumed run in a new process
    second.run("sh a.sh", cwd=ws)
    assert len(efs.created) == 1


def test_arguments_are_shell_quoted(ws):
    r, ac, _ = runner(ws)
    r.run(["python", "-c", "print('a; rm -rf ~')"], cwd=ws)
    assert ac.invocations[0]["arguments"]["command"].endswith("""python -c 'print('"'"'a; rm -rf ~'"'"')' < /dev/null""")


def test_denied_command_never_reaches_the_sandbox(ws):
    r, ac, efs = runner(ws)
    res = r.run("curl http://example.org", cwd=ws)
    assert res.denied and not ac.invocations and not ac.sessions and not efs.created


def test_cwd_outside_workspace_denied(ws, tmp_path):
    r, ac, _ = runner(ws)
    assert r.run("sh ok.sh", cwd=tmp_path).denied and not ac.invocations


def test_nonzero_exit_and_output_redaction(ws, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "supersecretvalue123")
    r, _, _ = runner(ws, FakeAgentCore({"stdout": "key=supersecretvalue123", "stderr": "boom", "exitCode": 3}))
    res = r.run("sh ok.sh", cwd=ws)
    assert res.exit_code == 3 and not res.ok and res.stderr == "boom"
    assert "supersecretvalue123" not in res.stdout


def test_missing_exit_code_is_not_success(ws):
    r, _, _ = runner(ws, FakeAgentCore({"stdout": "", "stderr": ""}))
    res = r.run("sh ok.sh", cwd=ws)
    assert res.exit_code is None and not res.ok and "no exit code" in res.stderr


def test_timeout_exit_maps_to_timed_out(ws, monkeypatch):
    r, ac, _ = runner(ws, FakeAgentCore({"stdout": "", "stderr": "", "exitCode": 124}))
    times = [0.0]  # first call = start; every later call is 400 s later
    monkeypatch.setattr("magi_system.execution.runner.time.monotonic", lambda: times.pop(0) if len(times) > 1 else 400.0)
    times.append(400.0)
    res = r.run("sh slow.sh", cwd=ws, timeout=300)
    assert res.timed_out and res.exit_code is None


def test_timeout_is_capped_below_the_sync_invoke_limit(ws):
    r, ac, _ = runner(ws)
    r.run("sh ok.sh", cwd=ws, timeout=3600)
    assert "timeout -k 5 840 " in ac.invocations[0]["arguments"]["command"]


def test_expired_session_is_restarted_once(ws):
    r, ac, _ = runner(ws, FakeAgentCore(fail_first=True))
    assert r.run("sh ok.sh", cwd=ws).ok
    assert len(ac.sessions) == 2


def test_close_stops_session_and_deletes_access_point(ws):
    r, ac, efs = runner(ws)
    r.run("sh ok.sh", cwd=ws)
    r.close()
    r.close()  # idempotent
    assert ac.stopped == ["s-1"] and efs.deleted == ["fsap-1"]


def test_unconfigured_runner_is_unavailable(ws):
    r, _, _ = runner(ws, env={})
    assert not r.available() and "MAGI_CI_IDENTIFIER" in r.unavailable_hint()


def test_agentcore_profile_allows_only_code_interpreter(ws):
    policy = PolicyEngine("agentcore")
    assert isinstance(make_runner(policy, ws), CodeInterpreterRunner)
    for other in ("local", "container"):
        with pytest.raises(PermissionError):
            make_runner(policy, ws, runner=other)


# -- toolchain: the target's wheels are installed from the runtime, the sandbox has no internet --------


class FakeUv:
    def __init__(self, code=0, stderr=""):
        self.calls, self.code, self.stderr = [], code, stderr

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.code == 0:
            site = cmd[cmd.index("--target") + 1]
            (__import__("pathlib").Path(site) / "pytest").mkdir()
        return __import__("types").SimpleNamespace(returncode=self.code, stdout="", stderr=self.stderr)


def _req(ws, text="fastapi==0.1\n# comment\npytest>=8\n", name="requirements.txt"):
    (ws / name).write_text(text)
    return [name]


def test_toolchain_installs_wheels_for_the_sandbox_platform(ws, monkeypatch):
    uv = FakeUv()
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", uv)
    r, _, _ = runner(ws)
    (ws / ".git").mkdir()
    r.prepare_toolchain(_req(ws))
    cmd = uv.calls[0]
    assert cmd[:3] == ["uv", "pip", "install"] and "--only-binary" in cmd and ":all:" in cmd
    assert cmd[cmd.index("--python-platform") + 1] == "aarch64-manylinux_2_28"
    assert cmd[cmd.index("--python-version") + 1] == "3.12"
    assert cmd[cmd.index("--target") + 1] == str(ws.resolve() / ".git" / "magi-toolchain" / "site")
    assert cmd[-1] == "pytest" and str(ws.resolve() / "requirements.txt") in cmd


def test_toolchain_is_idempotent_and_reinstalls_when_requirements_change(ws, monkeypatch):
    uv = FakeUv()
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", uv)
    r, _, _ = runner(ws)
    req = _req(ws)
    r.prepare_toolchain(req)
    r.prepare_toolchain(req)
    assert len(uv.calls) == 1
    _req(ws, "fastapi==0.2\n")
    r.prepare_toolchain(req)
    assert len(uv.calls) == 2


def test_toolchain_failure_carries_the_installer_output(ws, monkeypatch):
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", FakeUv(code=1, stderr="no wheel for foo"))
    r, _, _ = runner(ws)
    with pytest.raises(ToolchainError, match="exit 1.*no wheel for foo"):
        r.prepare_toolchain(_req(ws))
    assert not (ws / ".git" / "magi-toolchain" / "requirements.sha256").exists()  # a failed install is retried


@pytest.mark.parametrize("line", ["-r other.txt", "--index-url https://evil.example/simple", "pkg @ https://x/y.whl",
                                  "git+https://github.com/a/b", "./local", "/abs/path", "-e ."])
def test_toolchain_rejects_option_and_url_lines(ws, monkeypatch, line):
    uv = FakeUv()
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", uv)
    r, _, _ = runner(ws)
    with pytest.raises(ToolchainError, match="unsupported requirement line"):
        r.prepare_toolchain(_req(ws, f"fastapi==0.1\n{line}\n"))
    assert not uv.calls


def test_toolchain_requirements_must_be_inside_the_repository(ws, tmp_path):
    (tmp_path / "outside.txt").write_text("x\n")
    r, _, _ = runner(ws)
    for rel in ("../../../outside.txt", "missing.txt"):
        with pytest.raises(ToolchainError, match="not a file inside the repository"):
            r.prepare_toolchain([rel])


def test_no_requirements_is_a_noop_and_other_runners_refuse(ws, monkeypatch):
    uv = FakeUv()
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", uv)
    runner(ws)[0].prepare_toolchain([])
    assert not uv.calls
    local = make_runner(PolicyEngine("offline-test"), ws, runner="local")
    local.prepare_toolchain([])
    with pytest.raises(ToolchainError, match="only by the codeinterpreter runner"):
        local.prepare_toolchain(["requirements.txt"])
    with pytest.raises(ToolchainError, match="only by the codeinterpreter runner"):
        local.prepare_toolchain([], ["flake8"])


def test_extra_packages_are_installed_and_part_of_the_idempotence_key(ws, monkeypatch):
    uv = FakeUv()
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", uv)
    r, _, _ = runner(ws)
    req = _req(ws)
    r.prepare_toolchain(req, ["flake8==7.1.0"])
    assert uv.calls[0][-2:] == ["pytest", "flake8==7.1.0"]
    r.prepare_toolchain(req, ["flake8==7.1.0"])
    assert len(uv.calls) == 1
    r.prepare_toolchain(req, ["flake8==7.1.0", "black"])
    assert len(uv.calls) == 2


def test_packages_alone_need_no_requirements_file_and_reject_options(ws, monkeypatch):
    uv = FakeUv()
    monkeypatch.setattr("magi_system.execution.runner.subprocess.run", uv)
    r, _, _ = runner(ws)
    r.prepare_toolchain([], ["flake8"])
    assert uv.calls and uv.calls[0][-2:] == ["pytest", "flake8"]
    with pytest.raises(ToolchainError, match="unsupported requirement line"):
        r.prepare_toolchain([], ["--index-url=https://evil.example/simple"])
