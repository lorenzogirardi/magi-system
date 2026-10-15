import os

import pytest

from magi_system.execution import ContainerRunner, LocalRunner, make_runner
from magi_system.policy import PolicyEngine


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    (d / "ok.sh").write_text("echo hello\n")
    (d / "env.sh").write_text("env\n")
    (d / "slow.sh").write_text("sleep 5\n")
    return d


def test_local_runner_runs_allowed_command(ws):
    r = LocalRunner(PolicyEngine("offline-test"), ws).run("sh ok.sh", cwd=ws)
    assert r.ok and r.stdout.strip() == "hello" and r.runner == "local"


def test_unauthorized_command_is_denied_not_executed(ws):
    r = LocalRunner(PolicyEngine("offline-test"), ws).run("curl http://example.org", cwd=ws)
    assert r.denied and r.exit_code is None and not r.ok


def test_cwd_outside_workspace_denied(ws, tmp_path):
    r = LocalRunner(PolicyEngine("offline-test"), ws).run("sh ok.sh", cwd=tmp_path)
    assert r.denied


def test_timeout(ws):
    r = LocalRunner(PolicyEngine("offline-test"), ws).run("sh slow.sh", cwd=ws, timeout=0.5)
    assert r.timed_out and r.exit_code is None and not r.ok


def test_environment_scrubbed_and_redacted(ws, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "supersecretvalue123")
    r = LocalRunner(PolicyEngine("offline-test"), ws).run("sh env.sh", cwd=ws)
    assert "MY_API_TOKEN" not in r.stdout
    assert "supersecretvalue123" not in r.stdout


def test_default_profile_requires_container(ws):
    with pytest.raises(PermissionError):
        make_runner(PolicyEngine("default"), ws, runner="local")
    assert isinstance(make_runner(PolicyEngine("default"), ws), ContainerRunner)


def test_container_argv_isolation(ws):
    runner = ContainerRunner(PolicyEngine("default"), ws, "docker.io/library/debian:bookworm-slim", runtime="/usr/bin/podman")
    argv = runner.build_argv(("sh", "ok.sh"), ws)
    joined = " ".join(argv)
    assert "--network=none" in argv and "--cap-drop=ALL" in argv and "--pull=never" in argv
    assert f"--volume={ws.resolve()}:/workspace:rw" in argv
    assert sum(a.startswith("--volume") for a in argv) == 1  # only the workspace
    assert ".sock" not in joined and os.path.expanduser("~") + ":" not in joined


def test_docker_runs_as_caller_user_podman_does_not(ws):
    img = "docker.io/library/debian:bookworm-slim"
    docker = ContainerRunner(PolicyEngine("default"), ws, img, runtime="/usr/bin/docker").build_argv(("true",), ws)
    podman = ContainerRunner(PolicyEngine("default"), ws, img, runtime="/usr/bin/podman").build_argv(("true",), ws)
    assert f"--user={os.getuid()}:{os.getgid()}" in docker
    assert not any(a.startswith("--user") for a in podman)


def test_runtime_cli_env_is_minimal(ws, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "supersecretvalue123")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    env = ContainerRunner(PolicyEngine("default"), ws, "docker.io/library/debian:bookworm-slim").cli_env()
    assert "HOME" in env and env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert "MY_API_TOKEN" not in env


def test_container_image_must_be_allowlisted(ws):
    with pytest.raises(ValueError):
        ContainerRunner(PolicyEngine("default"), ws, "evil.example/img:latest")


@pytest.mark.skipif(
    not ContainerRunner(PolicyEngine("default"), __import__("pathlib").Path("/tmp"), "docker.io/library/debian:bookworm-slim").available(),
    reason="container image docker.io/library/debian:bookworm-slim not present locally (tests never pull)",
)
def test_container_runner_executes(ws):
    r = ContainerRunner(PolicyEngine("default"), ws, "docker.io/library/debian:bookworm-slim").run("sh ok.sh", cwd=ws)
    assert r.ok and "hello" in r.stdout
