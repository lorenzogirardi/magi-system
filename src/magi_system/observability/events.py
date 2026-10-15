"""Structured event log (JSONL per run), optionally mirrored to Langfuse.

Every event is redacted before it is written. Langfuse is optional: `LangfuseTracer`
imports the SDK lazily and is only created when tracing is enabled.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from magi_system.observability.redaction import Redactor


class LangfuseTracer:  # pragma: no cover - optional dependency, needs a Langfuse server
    """Mirrors redacted events to Langfuse. Configured only via LANGFUSE_* environment variables."""

    def __init__(self, run_id: str) -> None:
        try:
            from langfuse import Langfuse
        except ImportError as exc:
            raise RuntimeError("install the 'langfuse' extra to enable Langfuse tracing") from exc
        self._client = Langfuse()
        self._run_id = run_id

    def event(self, name: str, attributes: dict[str, Any]) -> None:
        self._client.create_event(name=name, metadata={"run_id": self._run_id, **attributes})


class EventLog:
    """Append-only, redacted JSONL event log for one run."""

    def __init__(self, path: Path | None, run_id: str, tracer: LangfuseTracer | None = None, redactor: Redactor | None = None):
        self.path = path
        self.run_id = run_id
        self.tracer = tracer
        self.redactor = redactor or Redactor()
        self._lock = threading.Lock()
        self.memory: list[dict[str, Any]] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, /, **data: Any) -> None:
        record = {**self.redactor.value(data), "ts": round(time.time(), 3), "run_id": self.run_id, "kind": event}
        line = json.dumps(record, default=str, sort_keys=True)
        with self._lock:
            self.memory.append(record)
            if self.path is not None:
                with self.path.open("a") as fh:
                    fh.write(line + "\n")
        if self.tracer is not None:
            self.tracer.event(event, record)

    def of_kind(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.memory if e["kind"] == kind]

    @staticmethod
    def read(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
