"""Helpers for graph-level tests. All runs use the scripted model and a temporary target
repository; they demonstrate orchestration behavior, not real model quality."""

from __future__ import annotations

import json
from pathlib import Path

from magi_system.models.common import AgentId
from magi_system.offline import DEMO_REQUEST, create_demo_repo, happy_path, scripted_models
from magi_system.service import RunService, RunSettings


class Run:
    def __init__(self, tmp: Path, scripts=None, *, files=None, settings: RunSettings | None = None, profile: str = "offline-test"):
        self.tmp = tmp
        self.repo = create_demo_repo(tmp / "target", files)
        self.scripts = scripts if scripts is not None else happy_path()
        self.models = scripted_models(self.scripts)
        self.service = RunService(tmp / "runs", models=self.models)
        self.settings = settings
        self.profile = profile

    def start(self, run_id: str = "t-run", request: str = DEMO_REQUEST, **kw):
        self.run_id = run_id
        self.outcome = self.service.start(repo=str(self.repo), ref="main", request_text=request, profile=self.profile,
                                          settings=self.settings, run_id=run_id, **kw)
        return self.outcome

    @property
    def model(self):
        return self.models[AgentId.DEV1]

    @property
    def state(self) -> dict:
        return self.service._values(self.run_id)

    def events(self, kind: str | None = None) -> list[dict]:
        path = self.service.workspaces.run_dir(self.run_id) / "events.jsonl"
        ev = [json.loads(line) for line in path.read_text().splitlines()]
        return [e for e in ev if kind is None or e["kind"] == kind]

    def calls(self, agent: AgentId | None = None, purpose: str | None = None):
        return [c for c in self.model.calls if (agent is None or c.agent == agent.value) and (purpose is None or c.purpose == purpose)]

    def gates(self) -> dict[str, str]:
        return {c.name: c.verdict.value for c in self.outcome.gate_checks}
