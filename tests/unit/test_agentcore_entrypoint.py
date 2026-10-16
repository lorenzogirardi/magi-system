"""AgentCore entrypoint dispatch, called directly (no HTTP server, no AWS, scripted model)."""

import time

import pytest

pytest.importorskip("bedrock_agentcore")

from magi_system.interfaces.agentcore import Dispatcher, load_openrouter_secret, make_app  # noqa: E402
from magi_system.offline import DEMO_REQUEST, create_demo_repo, scripted_models  # noqa: E402
from magi_system.service import RunService  # noqa: E402

OFFLINE_SETTINGS = {"default_agent": {"provider": "fake", "model": "scripted"}}


class Tasks:
    def __init__(self):
        self.active, self.done = {}, []

    def add_async_task(self, name, metadata=None):
        tid = len(self.active) + len(self.done) + 1
        self.active[tid] = metadata
        return tid

    def complete_async_task(self, tid):
        self.done.append(self.active.pop(tid))
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_ALLOWED_PROFILES", "offline-test")
    monkeypatch.setenv("MAGI_ALLOWED_PROVIDERS", "fake")
    repo = create_demo_repo(tmp_path / "greeter")
    tasks = Tasks()
    d = Dispatcher(lambda: RunService(tmp_path / "runs", models=scripted_models()), tasks)
    return d, tasks, repo


def start_payload(repo, **kw):
    return {"action": "start", "repo": str(repo), "ref": "main", "request": DEMO_REQUEST, "profile": "offline-test",
            "settings": OFFLINE_SETTINGS, "run_id": "ac-run", **kw}


def wait_idle(d, timeout=60):
    deadline = time.monotonic() + timeout
    while d.active is not None:
        assert time.monotonic() < deadline, "run did not finish"
        time.sleep(0.05)


def test_start_returns_immediately_then_status_and_report(env):
    d, tasks, repo = env
    resp = d.handle(start_payload(repo))
    assert resp == {"run_id": "ac-run", "accepted": True}
    assert d.active == "ac-run" or tasks.done  # registered as async task (HealthyBusy) while running
    wait_idle(d)
    assert tasks.done == [{"run_id": "ac-run"}] and not tasks.active
    st = d.handle({"action": "status", "run_id": "ac-run"})
    assert st["status"] == "SUCCESS" and st["active_in_session"] is False
    rep = d.handle({"action": "report", "run_id": "ac-run"})
    assert rep["report"]["outcome"]["status"] == "SUCCESS"
    assert "farewell()" in rep["candidate_patch"] and rep["patch_truncated"] is False


def test_second_run_in_same_session_is_rejected(env):
    d, _, repo = env
    d.active = "other-run"  # a run is already being driven by this process
    resp = d.handle(start_payload(repo))
    assert resp["error"] == "rejected" and "already active" in resp["detail"]


def test_profile_not_allowed_on_deployment(env, monkeypatch):
    d, _, repo = env
    monkeypatch.setenv("MAGI_ALLOWED_PROFILES", "agentcore")
    resp = d.handle(start_payload(repo))
    assert resp["error"] == "rejected" and "offline-test" in resp["detail"]


def test_only_openrouter_models_by_default(env, monkeypatch):
    d, _, repo = env
    monkeypatch.delenv("MAGI_ALLOWED_PROVIDERS")
    for provider in ("fake", "anthropic"):
        resp = d.handle(start_payload(repo, settings={"default_agent": {"provider": provider, "model": "m"}}))
        assert resp["error"] == "rejected" and "not allowed" in resp["detail"]
    resp = d.handle(start_payload(repo, settings={"default_agent": {"provider": "openrouter", "model": "m"},
                                                  "agents": {"magi.casper": {"provider": "anthropic", "model": "x"}}}))
    assert resp["error"] == "rejected" and "anthropic" in resp["detail"]


def test_invalid_payloads(env):
    d, _, repo = env
    assert d.handle({"action": "deploy"})["error"] == "invalid_request"
    assert d.handle({"action": "status", "run_id": "../etc"})["error"] == "invalid_request"
    assert d.handle({**start_payload(repo), "push": True})["error"] == "invalid_request"
    assert d.handle("not a dict")["error"] == "invalid_request"


def test_start_failure_is_reported_by_status(env):
    d, _, repo = env
    d.handle(start_payload(repo, ref="no-such-branch", run_id="bad-ref"))
    wait_idle(d)
    st = d.handle({"action": "status", "run_id": "bad-ref"})
    assert st["status"] == "START_FAILED" and "revision not found" in st["error"]
    assert d.handle({"action": "status", "run_id": "never"})["error"] == "unknown_run"


def test_make_app_registers_entrypoint(tmp_path):
    app = make_app(lambda: RunService(tmp_path / "runs", models=scripted_models()))
    assert app.handlers["main"] == app.dispatcher.handle
    assert app.get_current_ping_status().value == "Healthy"


def test_secret_loaded_from_secrets_manager(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("MAGI_OPENROUTER_SECRET_ID", "magi/openrouter")
    calls = []

    class Client:
        def get_secret_value(self, SecretId):
            calls.append(SecretId)
            return {"SecretString": "sk-or-test-value-1234567890\n"}

    load_openrouter_secret(Client)
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test-value-1234567890" and calls == ["magi/openrouter"]
    load_openrouter_secret(Client)  # already set: no second call
    assert calls == ["magi/openrouter"]


def test_ping_is_busy_while_a_run_is_active(tmp_path):
    import threading

    app = make_app(lambda: RunService(tmp_path / "runs", models=scripted_models()))
    release = threading.Event()
    app.dispatcher._background("busy-run", lambda: release.wait(10))
    assert app.get_current_ping_status().value == "HealthyBusy"  # keeps the session past the idle timeout
    release.set()
    wait_idle(app.dispatcher)
    assert app.get_current_ping_status().value == "Healthy"
