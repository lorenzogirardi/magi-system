"""Run store: run records (inputs, versions, status) and the LangGraph checkpointer, both
in one SQLite file under `runs_root`. Records are redacted and credential-free."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from magi_system.observability.redaction import Redactor


class RunStore:
    def __init__(self, runs_root: Path) -> None:
        runs_root.mkdir(parents=True, exist_ok=True)
        self.path = runs_root / "magi.sqlite"
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._redactor = Redactor()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, created REAL, updated REAL, "
                "status TEXT, record TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS effects (dedup_key TEXT PRIMARY KEY, run_id TEXT, performed REAL, detail TEXT)"
            )
            self._conn.commit()
        # State is JSON-native, so strict msgpack (no arbitrary class revival) is safe.
        self.checkpointer = SqliteSaver(self._conn, serde=JsonPlusSerializer(allowed_msgpack_modules=None))
        self.checkpointer.setup()

    def create(self, run_id: str, record: dict) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, created, updated, status, record) VALUES (?, ?, ?, ?, ?)",
                (run_id, now, now, "RUNNING", json.dumps(self._redactor.value(record), sort_keys=True)),
            )
            self._conn.commit()

    def update(self, run_id: str, *, status: str | None = None, **fields) -> None:
        rec = self.get(run_id)
        rec.update(self._redactor.value(fields))
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET updated = ?, status = COALESCE(?, status), record = ? WHERE run_id = ?",
                (time.time(), status, json.dumps(rec, sort_keys=True), run_id),
            )
            self._conn.commit()

    def get(self, run_id: str) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT status, record FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return {**json.loads(row[1]), "status": row[0]}

    # -- external-effect dedup (publication) ----------------------------------------------

    def record_effect(self, dedup_key: str, run_id: str, detail: str) -> bool:
        """Insert-once. Returns False if the effect was already recorded."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO effects (dedup_key, run_id, performed, detail) VALUES (?, ?, ?, ?)",
                (dedup_key, run_id, time.time(), detail),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def close(self) -> None:
        self._conn.close()
