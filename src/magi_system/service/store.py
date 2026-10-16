"""Run store: run records (inputs, versions, status), external-effect dedup and the
LangGraph checkpointer. Records are redacted and credential-free.

Two backends with the same interface, chosen by `open_store` from `MAGI_STORE`:
- `sqlite` (default): one SQLite file under `runs_root`.
- `dynamodb`: DynamoDB tables (+ optional S3 offload for large checkpoints), for hosted
  runs (AgentCore). Needs the `agentcore` extra. Configured only via environment variables.
"""

from __future__ import annotations

import json
import os
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


# -- DynamoDB backend ---------------------------------------------------------------------

# State is JSON-native, so strict msgpack (no arbitrary class revival) is safe; same as SQLite.
STRICT_SERDE = JsonPlusSerializer(allowed_msgpack_modules=None)


class StoreConfigError(Exception):
    """The store backend is misconfigured (unknown backend, missing env var or extra)."""


def _dynamo_saver_class():
    try:
        from langgraph_checkpoint_aws import DynamoDBSaver
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise StoreConfigError("MAGI_STORE=dynamodb needs the `agentcore` extra (uv sync --extra agentcore)") from exc

    class StrictDynamoDBSaver(DynamoDBSaver):
        # BaseCheckpointSaver.__init__ picks up `self.serde`; DynamoDBSaver takes no serde arg.
        serde = STRICT_SERDE

    return StrictDynamoDBSaver


class DynamoRunStore:
    """Run records and effects in one DynamoDB table (`pk`: `run#<id>` / `effect#<key>`);
    checkpoints in a second table managed by `langgraph-checkpoint-aws`."""

    def __init__(self, *, runs_table: str, checkpoint_table: str, region: str | None = None,
                 s3_bucket: str | None = None, endpoint_url: str | None = None, checkpoint_ttl_seconds: int | None = None) -> None:
        import boto3

        self._redactor = Redactor()
        self._table = runs_table
        self._client = boto3.client("dynamodb", region_name=region, endpoint_url=endpoint_url)
        offload = {"bucket_name": s3_bucket} if s3_bucket else None
        self.checkpointer = _dynamo_saver_class()(table_name=checkpoint_table, region_name=region, endpoint_url=endpoint_url,
                                                  s3_offload_config=offload, ttl_seconds=checkpoint_ttl_seconds)

    def create(self, run_id: str, record: dict) -> None:
        now = time.time()
        self._client.put_item(
            TableName=self._table,
            Item={"pk": {"S": f"run#{run_id}"}, "created": {"N": str(now)}, "updated": {"N": str(now)},
                  "status": {"S": "RUNNING"}, "record": {"S": json.dumps(self._redactor.value(record), sort_keys=True)}},
            ConditionExpression="attribute_not_exists(pk)",
        )

    def update(self, run_id: str, *, status: str | None = None, **fields) -> None:
        current = self.get(run_id)
        rec = {k: v for k, v in current.items() if k != "status"}
        rec.update(self._redactor.value(fields))
        self._client.put_item(
            TableName=self._table,
            Item={"pk": {"S": f"run#{run_id}"}, "updated": {"N": str(time.time())},
                  "status": {"S": status or current["status"]}, "record": {"S": json.dumps(rec, sort_keys=True)}},
        )

    def get(self, run_id: str) -> dict:
        item = self._client.get_item(TableName=self._table, Key={"pk": {"S": f"run#{run_id}"}}, ConsistentRead=True).get("Item")
        if item is None:
            raise KeyError(run_id)
        return {**json.loads(item["record"]["S"]), "status": item["status"]["S"]}

    def record_effect(self, dedup_key: str, run_id: str, detail: str) -> bool:
        """Insert-once. Returns False if the effect was already recorded."""
        try:
            self._client.put_item(
                TableName=self._table,
                Item={"pk": {"S": f"effect#{dedup_key}"}, "run_id": {"S": run_id}, "performed": {"N": str(time.time())},
                      "detail": {"S": detail}},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def close(self) -> None:
        pass


def open_store(runs_root: Path):
    """Store backend selected by `MAGI_STORE` (`sqlite` default, or `dynamodb`)."""
    backend = os.environ.get("MAGI_STORE", "sqlite")
    if backend == "sqlite":
        return RunStore(runs_root)
    if backend == "dynamodb":
        missing = [k for k in ("MAGI_DYNAMODB_RUNS_TABLE", "MAGI_DYNAMODB_CHECKPOINT_TABLE") if not os.environ.get(k)]
        if missing:
            raise StoreConfigError(f"MAGI_STORE=dynamodb requires {', '.join(missing)}")
        return DynamoRunStore(
            runs_table=os.environ["MAGI_DYNAMODB_RUNS_TABLE"],
            checkpoint_table=os.environ["MAGI_DYNAMODB_CHECKPOINT_TABLE"],
            region=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
            s3_bucket=os.environ.get("MAGI_CHECKPOINT_S3_BUCKET") or None,
            endpoint_url=os.environ.get("MAGI_DYNAMODB_ENDPOINT_URL") or None,
            checkpoint_ttl_seconds=int(os.environ["MAGI_CHECKPOINT_TTL_SECONDS"]) if os.environ.get("MAGI_CHECKPOINT_TTL_SECONDS") else None,
        )
    raise StoreConfigError(f"unknown MAGI_STORE backend {backend!r} (expected sqlite or dynamodb)")
