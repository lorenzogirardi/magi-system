import os

import pytest

from magi_system.execution import LocalRunner
from magi_system.models import AgentId
from magi_system.policy import PolicyEngine
from magi_system.tools import Toolbox, ToolError


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    (d / "src").mkdir(parents=True)
    (d / "docs").mkdir()
    (d / ".magi").mkdir()
    (d / "src/a.sh").write_text("greet() { echo hi; }\n")
    (d / ".magi/config.yaml").write_text("languages: [shell]\n")
    (d / ".git").mkdir()
    (d / ".git/config").write_text("[core]\n")
    return d


def box(ws, role, **kw):
    policy = PolicyEngine("offline-test")
    return Toolbox(role=role, workspace=ws, policy=policy, runner=LocalRunner(policy, ws), **kw)


def test_magi_is_read_only(ws):
    t = box(ws, AgentId.MELCHIOR)
    assert set(t.available()) == {"list_files", "read_file", "grep"}
    assert "greet" in t.call("read_file", {"path": "src/a.sh"})["content"]
    with pytest.raises(ToolError):
        t.call("write_file", {"path": "src/a.sh", "content": "x"})
    with pytest.raises(ToolError):
        t.call("run_command", {"command": "sh src/a.sh"})


def test_dev_writes_only_in_scope_and_never_protected(ws):
    t = box(ws, AgentId.DEV1, task_scopes=("src/**",), doc_scopes=("docs/**",))
    t.call("write_file", {"path": "src/b.sh", "content": "echo b\n"})
    assert (ws / "src/b.sh").is_file()
    for path in ("docs/x.md", ".magi/config.yaml", ".git/config", "../escape.txt"):
        with pytest.raises(ToolError):
            t.call("write_file", {"path": path, "content": "x"})
    assert (ws / ".magi/config.yaml").read_text() == "languages: [shell]\n"


def test_symlink_escape_blocked(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, ws / "src/link.txt")
    t = box(ws, AgentId.DEV1, task_scopes=("src/**",))
    with pytest.raises(ToolError):
        t.call("read_file", {"path": "src/link.txt"})
    with pytest.raises(ToolError):
        t.call("write_file", {"path": "src/link.txt", "content": "x"})
    assert outside.read_text() == "secret"


def test_tech_writer_doc_scope_only(ws):
    t = box(ws, AgentId.TECH_WRITER, task_scopes=("**",), doc_scopes=("docs/**",))
    t.call("write_file", {"path": "docs/new.md", "content": "# x\n"})
    with pytest.raises(ToolError):
        t.call("write_file", {"path": "src/a.sh", "content": "x"})
    assert "run_command" not in t.available()


def test_git_dir_hidden(ws):
    t = box(ws, AgentId.MELCHIOR)
    assert not any(f.startswith(".git/") for f in t.call("list_files", {})["files"])
    with pytest.raises(ToolError):
        t.call("read_file", {"path": ".git/config"})


def test_commands_go_through_policy(ws):
    t = box(ws, AgentId.DEV1, task_scopes=("src/**",))
    assert t.call("run_command", {"command": "sh src/a.sh"})["exit_code"] == 0
    with pytest.raises(ToolError):
        t.call("run_command", {"command": "curl http://example.org"})


def test_budget_hook_called(ws):
    seen = []
    t = box(ws, AgentId.MELCHIOR, on_call=seen.append)
    t.call("list_files", {})
    assert seen == ["list_files"]
