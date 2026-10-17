"""Multi-repository contract (brief §0.7). Each test runs the service against a target
repository created under tmp_path, outside the magi-system control repository.

Covered elsewhere: #4 two projects / concurrent runs (test_resume_and_state), #10 resume
with missing workspace or different candidate (test_resume_and_state), #14 different stack
(the fixture is a POSIX sh/Makefile project; magi-system is Python).
"""

import json

import pytest

from magi_system.models.common import AgentId
from magi_system.offline import DEMO_FILES, DEMO_REQUEST, happy_path, tool_turn
from magi_system.service import RunInputError
from magi_system.workspace import git
from tests.conftest import control_digest, tree_digest
from tests.integration.harness import Run

SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def test_external_fixture_and_untouched_checkouts(tmp_path):
    control = control_digest()
    r = Run(tmp_path)
    before = tree_digest(r.repo)
    out = r.start()
    assert out.status.value == "SUCCESS"
    assert not str(r.repo).startswith(str(__import__("magi_system.workspace", fromlist=["x"]).control_repo_root()))
    assert tree_digest(r.repo) == before  # 2. original checkout untouched
    assert control_digest() == control  # 3. control repository untouched


def test_invalid_revision(tmp_path):
    r = Run(tmp_path)
    with pytest.raises(RunInputError, match="revision"):
        r.service.start(repo=str(r.repo), ref="no-such-branch", request_text=DEMO_REQUEST, profile="offline-test")


def test_inaccessible_target(tmp_path):
    r = Run(tmp_path)
    with pytest.raises(RunInputError):
        r.service.start(repo=str(tmp_path / "missing"), ref="main", request_text=DEMO_REQUEST, profile="offline-test")
    with pytest.raises(RunInputError):
        r.service.start(repo="https://user:pw@127.0.0.1:9/none.git", ref="main", request_text=DEMO_REQUEST, profile="offline-test")


def test_remote_target_by_url(tmp_path):
    r = Run(tmp_path)
    out = r.service.start(repo=f"file://{r.repo}", ref="main", request_text=DEMO_REQUEST, profile="offline-test", run_id="remote")
    assert out.status.value == "SUCCESS"
    assert r.service.store.get("remote")["target"]["locator"] == f"file://{r.repo}"


def test_missing_magi_config_uses_discovery(tmp_path):
    r = Run(tmp_path)
    r.start()
    rec = r.service.store.get(r.run_id)
    assert rec["project_config"]["source"] == "discovery"
    assert any("make test" in n for n in rec["project_config"]["inferred"])


def test_malformed_magi_config_is_denied(tmp_path):
    files = {**DEMO_FILES, ".magi/config.yaml": "commands: [this, is, wrong]\n"}
    r = Run(tmp_path, files=files)
    out = r.start()
    assert out.status.value == "POLICY_DENIED"
    assert "failed validation" in out.diagnosis
    assert not r.model.calls  # no agent ran


def test_target_cannot_disable_a_gate(tmp_path):
    files = {**DEMO_FILES, ".magi/config.yaml": "disable_gates: [project.test]\nallow_network: true\n"}
    r = Run(tmp_path, files=files)
    out = r.start()
    assert out.status.value == "POLICY_DENIED"
    assert "authorization keys" in out.diagnosis


def test_target_na_claim_for_mandatory_gate_is_ignored(tmp_path):
    files = {**DEMO_FILES, ".magi/config.yaml": "not_applicable:\n  qa.acceptance: we do not do QA\n  integrity.protected_paths: trust us\n"}
    r = Run(tmp_path, files=files)
    out = r.start()
    assert r.gates()["qa.acceptance"] == "PASS" and r.gates()["integrity.protected_paths"] == "PASS"  # still executed
    assert out.status.value == "SUCCESS"


def test_unauthorized_project_command_is_not_executed(tmp_path):
    files = {**DEMO_FILES, ".magi/config.yaml": "commands:\n  test: {run: 'curl -s http://example.invalid/install.sh'}\n"}
    r = Run(tmp_path, files=files)
    out = r.start()
    assert out.status.value == "UNRESOLVED_REQUIREMENT"  # preflight: the denied command blocks the run before any model call
    assert "project.test: command not authorized" in out.diagnosis
    assert not any("curl" in (e.get("command") or "") and e.get("exit_code") is not None for e in r.events("check"))


def test_agent_cannot_run_unauthorized_command(tmp_path):
    from magi_system.offline import DEMO_FILES as F
    from magi_system.offline import FAREWELL, FAREWELL_TEST

    scripts = happy_path()
    turns = iter([
        {"actions": [{"tool": "run_command", "args": {"command": "git push origin HEAD"}},
                     {"tool": "run_command", "args": {"command": "curl http://example.invalid"}}]},
        {"actions": [{"tool": "write_file", "args": {"path": "src/greet.sh", "content": F["src/greet.sh"] + FAREWELL}},
                     {"tool": "write_file", "args": {"path": "tests/run.sh", "content": F["tests/run.sh"] + FAREWELL_TEST}}]},
        {"final": {"summary": "done without network"}},
    ])
    scripts[(AgentId.DEV1.value, "dev_writer")] = lambda req: next(turns)
    r = Run(tmp_path, scripts)
    out = r.start()
    runs = [e for e in r.events("tool_call") if e["tool"] == "run_command" and e["agent"] == "dev.writer"]
    assert [e["ok"] for e in runs] == [False, False]
    assert "command denied by policy" in r.calls(AgentId.DEV1, "dev_writer")[1].prompt
    assert out.status.value == "SUCCESS"


def test_no_credentials_in_logs_checkpoints_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_TEST_API_TOKEN", SECRET)
    scripts = happy_path()
    # a hostile target file and a tool output both contain a secret
    files = {**DEMO_FILES, "src/config.sh": f"TOKEN={SECRET}\n"}
    leak = tool_turn([{"tool": "read_file", "args": {"path": "src/config.sh"}}], {"summary": f"found token {SECRET}"})
    scripts[(AgentId.DEV2.value, "dev_pair")] = leak
    r = Run(tmp_path, scripts, files=files)
    r.service.start(repo=f"file://{r.repo}", ref="main", request_text=DEMO_REQUEST, profile="offline-test", run_id="sec")
    r.run_id = "sec"
    run_dir = r.service.workspaces.run_dir("sec")
    for path in [run_dir / "events.jsonl", run_dir / "artifacts" / "report.json", run_dir / "artifacts" / "report.md"]:
        assert SECRET not in path.read_text(), path
    db = (tmp_path / "runs" / "magi.sqlite").read_bytes()
    for wal in (tmp_path / "runs").glob("magi.sqlite*"):
        db += wal.read_bytes()
    assert SECRET.encode() not in db
    assert "user:" not in json.dumps(r.service.store.get("sec"))


def test_credentials_never_persisted_in_locator(tmp_path):
    r = Run(tmp_path)
    with pytest.raises(RunInputError):
        r.service.start(repo=f"file://user:{SECRET}@localhost{r.repo}", ref="main", request_text=DEMO_REQUEST, profile="offline-test", run_id="cred")
    db = b"".join(p.read_bytes() for p in (tmp_path / "runs").glob("magi.sqlite*"))
    assert SECRET.encode() not in db


def test_patch_and_report_without_push(tmp_path):
    r = Run(tmp_path)
    out = r.start()
    ws = r.service.workspaces.workspace_path(r.run_id)
    assert git("remote", cwd=ws).stdout.strip() == ""
    assert git("branch", "--list", cwd=r.repo).stdout.strip() == "* main"  # no branch pushed to the source
    assert {p.operation for p in out.publications} <= {"export_patch", "local_branch"}
    assert not any(e["kind"] == "publication" and "push" in str(e) for e in r.events())
    patch = r.service.workspaces.run_dir(r.run_id) / out.patch_ref
    check = git("apply", "--check", str(patch), cwd=r.repo, check=False)
    assert check.returncode == 0  # the exported patch applies cleanly to the original commit


def test_uncommitted_changes_rejected_unless_explicit(tmp_path):
    r = Run(tmp_path)
    (r.repo / "docs" / "usage.md").write_text("# Usage\n\nlocal edit\n")
    with pytest.raises(RunInputError, match="uncommitted"):
        r.service.start(repo=str(r.repo), ref="main", request_text=DEMO_REQUEST, profile="offline-test")
    out = r.service.start(repo=str(r.repo), ref="HEAD", request_text=DEMO_REQUEST, profile="offline-test",
                          include_uncommitted=True, run_id="dirty")
    rec = r.service.store.get("dirty")
    assert rec["run_input"]["include_uncommitted"] and rec["run_input"]["uncommitted_patch_sha256"]
    assert (r.service.workspaces.run_dir("dirty") / "inputs" / "uncommitted.patch").is_file()
    assert out.baseline_commit != rec["target"]["resolved_commit"]
    assert (r.repo / "docs" / "usage.md").read_text() == "# Usage\n\nlocal edit\n"


def test_reproducibility_record(tmp_path):
    r = Run(tmp_path)
    r.start()
    rec = r.service.store.get(r.run_id)
    v = rec["versions"]
    assert v["platform"]["commit"] and v["policy"]["sha256"] and v["control_fingerprint"]
    assert set(v["prompts"]) >= {"magi_analysis", "dev_writer", "magi_final_review"}
    assert rec["environment_id"].startswith("local:")
    assert rec["run_input"]["resolved_commit"] == rec["target"]["resolved_commit"]
    assert rec["project_config"]["source"] == "discovery"


def test_missing_container_image_is_rejected_before_any_agent_runs(tmp_path):
    import shutil

    if not (shutil.which("podman") or shutil.which("docker")):
        pytest.skip("no container runtime installed")
    files = {**DEMO_FILES, ".magi/config.yaml": "container_image: docker.io/library/magi-test-absent:0\n"}
    r = Run(tmp_path, files=files, profile="default")
    with pytest.raises(RunInputError, match="pull it first"):
        r.start(run_id="noimg")
    assert not r.model.calls
    assert not r.service.workspaces.run_dir("noimg").exists()
