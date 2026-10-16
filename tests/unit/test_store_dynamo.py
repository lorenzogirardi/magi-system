"""DynamoDB store backend (MAGI_STORE=dynamodb) against moto's in-process AWS mock.

No AWS account or network is used: moto intercepts every boto3 call.
"""

import pytest

pytest.importorskip("langgraph_checkpoint_aws")
moto = pytest.importorskip("moto")
import boto3  # noqa: E402

from magi_system.models.common import AgentId  # noqa: E402
from magi_system.offline import happy_path  # noqa: E402
from magi_system.service.store import DynamoRunStore, StoreConfigError, open_store  # noqa: E402
from tests.integration.harness import Run  # noqa: E402

REGION = "eu-west-1"


def _tables(runs: str = "magi-runs", checkpoints: str = "magi-checkpoints", bucket: str = "magi-offload") -> None:
    ddb = boto3.client("dynamodb", region_name=REGION)
    ddb.create_table(TableName=runs, BillingMode="PAY_PER_REQUEST",
                     AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}])
    # Key schema required by langgraph-checkpoint-aws DynamoDBSaver (unified table design).
    ddb.create_table(TableName=checkpoints, BillingMode="PAY_PER_REQUEST",
                     AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
                 "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION, "AWS_CONFIG_FILE": "/dev/null",
                 "AWS_SHARED_CREDENTIALS_FILE": "/dev/null"}.items():
        monkeypatch.setenv(k, v)
    with moto.mock_aws():
        _tables()
        yield


@pytest.fixture
def dynamo_env(aws, monkeypatch):
    monkeypatch.setenv("MAGI_STORE", "dynamodb")
    monkeypatch.setenv("MAGI_DYNAMODB_RUNS_TABLE", "magi-runs")
    monkeypatch.setenv("MAGI_DYNAMODB_CHECKPOINT_TABLE", "magi-checkpoints")
    monkeypatch.setenv("MAGI_CHECKPOINT_S3_BUCKET", "magi-offload")


def test_record_lifecycle_and_redaction(aws):
    store = DynamoRunStore(runs_table="magi-runs", checkpoint_table="magi-checkpoints", region=REGION)
    store.create("r1", {"target": {"locator": "https://user:hunter2secret@example.org/x.git"}, "n": 1})
    rec = store.get("r1")
    assert rec["status"] == "RUNNING" and rec["n"] == 1
    assert "hunter2secret" not in str(rec)
    store.update("r1", status="SUCCESS", outcome={"ok": True})
    rec = store.get("r1")
    assert rec["status"] == "SUCCESS" and rec["outcome"] == {"ok": True} and rec["n"] == 1
    store.update("r1", note="x")  # status unchanged when not given
    assert store.get("r1")["status"] == "SUCCESS"
    with pytest.raises(KeyError):
        store.get("missing")


def test_create_refuses_existing_run(aws):
    store = DynamoRunStore(runs_table="magi-runs", checkpoint_table="magi-checkpoints", region=REGION)
    store.create("r1", {})
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError, match="ConditionalCheckFailed"):
        store.create("r1", {})


def test_effect_recorded_once(aws):
    store = DynamoRunStore(runs_table="magi-runs", checkpoint_table="magi-checkpoints", region=REGION)
    assert store.record_effect("export_patch:r1:abc", "r1", "p") is True
    assert store.record_effect("export_patch:r1:abc", "r1", "p") is False


def test_open_store_selection(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGI_STORE", raising=False)
    assert type(open_store(tmp_path)).__name__ == "RunStore"
    monkeypatch.setenv("MAGI_STORE", "dynamodb")
    monkeypatch.delenv("MAGI_DYNAMODB_RUNS_TABLE", raising=False)
    with pytest.raises(StoreConfigError, match="MAGI_DYNAMODB_RUNS_TABLE"):
        open_store(tmp_path)
    monkeypatch.setenv("MAGI_STORE", "postgres")
    with pytest.raises(StoreConfigError, match="unknown"):
        open_store(tmp_path)


def test_full_offline_run_on_dynamodb(tmp_path, dynamo_env):
    r = Run(tmp_path)
    assert isinstance(r.service.store, DynamoRunStore)
    out = r.start()
    assert out.status.value == "SUCCESS", out.diagnosis
    assert r.service.status(r.run_id)["status"] == "SUCCESS"
    assert r.service.report(r.run_id)["outcome"]["final_commit"] == out.final_commit


def test_resume_after_crash_on_dynamodb(tmp_path, dynamo_env):
    scripts = happy_path()
    flag = {"done": False}
    original = scripts[(AgentId.DEV2.value, "pair_assessment")]

    def crash_once(req):
        if not flag["done"]:
            flag["done"] = True
            raise RuntimeError("simulated process crash")
        return original(req)

    scripts[(AgentId.DEV2.value, "pair_assessment")] = crash_once
    r = Run(tmp_path, scripts)
    out = r.start()
    assert out.status.value == "INFRA_FAILURE" and r.service.status(r.run_id)["resumable"] is True
    writer_calls = len(r.calls(AgentId.DEV1))
    out2 = r.service.resume(r.run_id)
    assert out2.status.value == "SUCCESS", out2.diagnosis
    assert len(r.calls(AgentId.DEV1)) == writer_calls  # writer not re-run: checkpoint came from DynamoDB
