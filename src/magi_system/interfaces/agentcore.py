"""Amazon Bedrock AgentCore Runtime entrypoint (`magi-agentcore`). Thin layer over `RunService`.

AgentCore hosts the process; models are always called through OpenRouter (never Bedrock
models). A run takes longer than the 15-minute synchronous invocation limit, so `start` and
`resume` run in a background thread registered as an AgentCore async task (the `/ping`
endpoint reports HealthyBusy while it runs) and return immediately; `status` and `report`
read the durable store, so any session can answer them. See docs/agentcore/api.md.

Configuration comes only from environment variables:
  MAGI_RUNS_ROOT            run workspaces/artifacts (EFS mount on AgentCore)
  MAGI_ALLOWED_PROFILES     comma list of policy profiles callers may request (default: agentcore)
  MAGI_ALLOWED_PROVIDERS    comma list of model providers runs may use (default: openrouter)
  MAGI_SETTINGS_FILE        run settings YAML used when the payload has no `settings`
  MAGI_OPENROUTER_SECRET_ID Secrets Manager id holding the OpenRouter key (if OPENROUTER_API_KEY unset)
"""

from __future__ import annotations

import os
import re
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from magi_system.service import ResumeError, RunInputError, RunService, RunSettings

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_PATCH_BYTES = 5_000_000  # inline in the report response; the payload limit is 100 MB


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRequest(_Req):
    action: Literal["start"]
    repo: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    request: str = Field(min_length=1)
    profile: str = "agentcore"
    project: str | None = None
    settings: dict[str, Any] | None = None
    run_id: str | None = Field(default=None, pattern=RUN_ID_RE.pattern)


class RunRequest(_Req):
    action: Literal["resume", "status", "report"]
    run_id: str = Field(pattern=RUN_ID_RE.pattern)


Request = TypeAdapter(Annotated[StartRequest | RunRequest, Field(discriminator="action")])


def _csv_env(name: str, default: str) -> set[str]:
    return {v.strip() for v in os.environ.get(name, default).split(",") if v.strip()}


def load_openrouter_secret(client_factory: Callable[[], Any] | None = None) -> None:
    """Put the OpenRouter key from Secrets Manager into the environment, once, before any
    `Redactor` is built (redactors read secret-looking env values when created)."""
    secret_id = os.environ.get("MAGI_OPENROUTER_SECRET_ID")
    if os.environ.get("OPENROUTER_API_KEY") or not secret_id:
        return
    if client_factory is None:
        import boto3

        def client_factory():
            return boto3.client("secretsmanager")

    value = client_factory().get_secret_value(SecretId=secret_id)["SecretString"].strip()
    if not value:
        raise RuntimeError(f"secret {secret_id} is empty")
    os.environ["OPENROUTER_API_KEY"] = value


def _settings(req: StartRequest) -> RunSettings:
    if req.settings is not None:
        return RunSettings.model_validate(req.settings)
    path = os.environ.get("MAGI_SETTINGS_FILE")
    return RunSettings.load(Path(path) if path else None)


def _check_providers(settings: RunSettings) -> None:
    allowed = _csv_env("MAGI_ALLOWED_PROVIDERS", "openrouter")
    used = {settings.default_agent.provider} | {a.provider for a in settings.agents.values()}
    if not used <= allowed:
        raise RunInputError(f"model provider(s) {sorted(used - allowed)} not allowed here (allowed: {sorted(allowed)})")


class Dispatcher:
    """Request handling and the one-active-run-per-process rule (one session = one run)."""

    def __init__(self, service_factory: Callable[[], RunService], tasks) -> None:
        self._factory = service_factory
        self._service: RunService | None = None
        self._tasks = tasks  # BedrockAgentCoreApp: add_async_task / complete_async_task
        self._lock = threading.Lock()
        self.active: str | None = None
        self.start_failures: dict[str, str] = {}

    @property
    def service(self) -> RunService:
        if self._service is None:
            self._service = self._factory()
        return self._service

    def handle(self, payload: Any) -> dict:
        try:
            req = Request.validate_python(payload)
        except ValidationError as exc:
            return {"error": "invalid_request", "detail": exc.errors(include_url=False, include_input=False)}
        try:
            if isinstance(req, StartRequest):
                return self._start(req)
            if req.action == "resume":
                return self._background(req.run_id, lambda: self.service.resume(req.run_id))
            if req.action == "status":
                return self._status(req.run_id)
            return self._report(req.run_id)
        except (RunInputError, ResumeError) as exc:
            return {"error": "rejected", "detail": str(exc)}

    def _start(self, req: StartRequest) -> dict:
        if req.profile not in _csv_env("MAGI_ALLOWED_PROFILES", "agentcore"):
            raise RunInputError(f"policy profile {req.profile!r} is not allowed on this deployment")
        settings = _settings(req)
        _check_providers(settings)
        run_id = req.run_id or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        return self._background(run_id, lambda: self.service.start(
            repo=req.repo, ref=req.ref, request_text=req.request, profile=req.profile, settings=settings,
            project_identity=req.project, run_id=run_id))

    def _background(self, run_id: str, work: Callable[[], Any]) -> dict:
        with self._lock:
            if self.active is not None:
                raise RunInputError(f"run {self.active} is already active in this session; use one session per run")
            self.active = run_id
        task = self._tasks.add_async_task("magi-run", {"run_id": run_id})

        def body() -> None:
            try:
                work()
            except (RunInputError, ResumeError) as exc:  # rejected before the graph ran (bad ref, sandbox config ...)
                self.start_failures[run_id] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # unexpected crash outside the graph's own handling
                self.start_failures[run_id] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            finally:
                with self._lock:
                    self.active = None
                self._tasks.complete_async_task(task)

        threading.Thread(target=body, name=f"magi-{run_id}", daemon=True).start()
        return {"run_id": run_id, "accepted": True}

    def _status(self, run_id: str) -> dict:
        try:
            out = self.service.status(run_id)
        except KeyError:
            if run_id in self.start_failures:
                return {"run_id": run_id, "status": "START_FAILED", "error": self.start_failures[run_id]}
            if run_id == self.active:
                return {"run_id": run_id, "status": "STARTING"}
            return {"error": "unknown_run", "run_id": run_id}
        return {**out, "active_in_session": run_id == self.active}

    def _report(self, run_id: str) -> dict:
        try:
            rep = self.service.report(run_id)
        except KeyError:
            return {"error": "unknown_run", "run_id": run_id}
        patch = self.service.workspaces.artifacts_path(run_id) / "candidate.patch"
        text = patch.read_text() if patch.is_file() and patch.stat().st_size <= MAX_PATCH_BYTES else ""
        return {"report": rep, "candidate_patch": text,
                "patch_truncated": patch.is_file() and patch.stat().st_size > MAX_PATCH_BYTES}


def make_app(service_factory: Callable[[], RunService] | None = None):
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()
    if service_factory is None:
        def service_factory() -> RunService:
            load_openrouter_secret()
            return RunService(Path(os.environ.get("MAGI_RUNS_ROOT", "/mnt/runs")))

    dispatcher = Dispatcher(service_factory, app)
    app.entrypoint(dispatcher.handle)
    app.dispatcher = dispatcher
    return app


def main() -> None:  # pragma: no cover - process entry point
    os.environ.setdefault("MAGI_EVENTS_STDOUT", "1")
    load_openrouter_secret()
    make_app().run(port=int(os.environ.get("PORT", "8080")), host="0.0.0.0")
