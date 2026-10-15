import pytest

from magi_system.workspace import (
    InvalidRevisionError,
    TargetInaccessibleError,
    UncommittedChangesError,
    WorkspaceError,
    WorkspaceManager,
    git,
)
from tests.conftest import CONTROL_ROOT, make_repo, tree_digest


def test_prepare_isolated_clone_without_touching_source(workspaces, target_repo):
    before = tree_digest(target_repo)
    m = workspaces.prepare("run-a", str(target_repo), "main")
    ws = workspaces.workspace_path("run-a")
    assert m.resolved_commit == git("rev-parse", "main", cwd=target_repo).stdout.strip()
    assert m.baseline_commit == m.resolved_commit
    assert (ws / "src/greet.sh").is_file()
    assert git("remote", cwd=ws).stdout.strip() == ""  # no push target available
    assert git("branch", "--show-current", cwd=ws).stdout.strip() == "magi/run-a"
    (ws / "src/new.sh").write_text("echo hi\n")
    workspaces.commit("run-a", "add file", "dev.writer", "dev1:1")
    assert tree_digest(target_repo) == before


def test_invalid_revision(workspaces, target_repo):
    with pytest.raises(InvalidRevisionError):
        workspaces.prepare("run-b", str(target_repo), "does-not-exist")
    with pytest.raises(InvalidRevisionError):
        workspaces.prepare("run-c", str(target_repo), "--upload-pack=evil")
    assert not workspaces.run_dir("run-b").exists()


def test_inaccessible_targets(workspaces, tmp_path):
    with pytest.raises(TargetInaccessibleError):
        workspaces.prepare("r1", str(tmp_path / "missing"), "main")
    (tmp_path / "plain").mkdir()
    with pytest.raises(TargetInaccessibleError):
        workspaces.prepare("r2", str(tmp_path / "plain"), "main")
    with pytest.raises(TargetInaccessibleError):
        workspaces.prepare("r3", "file:///nonexistent/repo.git", "main")


def test_remote_locator_via_file_url(workspaces, target_repo):
    m = workspaces.prepare("run-remote", f"file://{target_repo}", "main")
    assert m.locator_kind == "remote"
    assert m.locator == f"file://{target_repo}"


def test_uncommitted_changes_rejected_unless_explicit(workspaces, target_repo):
    (target_repo / "src/greet.sh").write_text("# dirty\n")
    (target_repo / "untracked.txt").write_text("u\n")
    before = tree_digest(target_repo)
    with pytest.raises(UncommittedChangesError):
        workspaces.prepare("run-d", str(target_repo), "main")
    m = workspaces.prepare("run-e", str(target_repo), "HEAD", include_uncommitted=True)
    assert m.uncommitted_patch_sha256
    assert m.baseline_commit != m.resolved_commit
    ws = workspaces.workspace_path("run-e")
    assert (ws / "src/greet.sh").read_text() == "# dirty\n"
    assert (ws / "untracked.txt").is_file()
    assert (workspaces.run_dir("run-e") / "inputs/uncommitted.patch").is_file()
    assert tree_digest(target_repo) == before


def test_commit_is_idempotent_per_step(workspaces, target_repo):
    workspaces.prepare("run-f", str(target_repo), "main")
    ws = workspaces.workspace_path("run-f")
    (ws / "a.txt").write_text("1\n")
    c1 = workspaces.commit("run-f", "step", "dev.writer", "dev1:r0:1")
    c2 = workspaces.commit("run-f", "step again", "dev.writer", "dev1:r0:1")
    assert c1 == c2
    assert workspaces.find_step_commit("run-f", "dev1:r0:1") == c1


def test_restore_discards_partial_writes(workspaces, target_repo):
    m = workspaces.prepare("run-g", str(target_repo), "main")
    ws = workspaces.workspace_path("run-g")
    (ws / "partial.txt").write_text("x")
    (ws / "src/greet.sh").write_text("broken")
    workspaces.restore("run-g", m.baseline_commit)
    assert not (ws / "partial.txt").exists()
    assert "greet()" in (ws / "src/greet.sh").read_text()


def test_two_runs_two_projects_isolated(workspaces, tmp_path):
    a = make_repo(tmp_path / "proj-a", {"a.txt": "A\n"})
    b = make_repo(tmp_path / "proj-b", {"b.txt": "B\n"})
    workspaces.prepare("run-1", str(a), "main")
    workspaces.prepare("run-2", str(b), "main")
    (workspaces.workspace_path("run-1") / "a.txt").write_text("changed\n")
    workspaces.commit("run-1", "x", "dev.writer", "s")
    assert (workspaces.workspace_path("run-2") / "b.txt").read_text() == "B\n"
    assert not (workspaces.workspace_path("run-2") / "a.txt").exists()


def test_runs_root_cannot_be_inside_control_repo():
    with pytest.raises(WorkspaceError):
        WorkspaceManager(CONTROL_ROOT / ".runs")


def test_run_id_cannot_escape(workspaces):
    with pytest.raises(WorkspaceError):
        workspaces.run_dir("../other")


def test_lock_prevents_concurrent_drivers(workspaces, target_repo):
    workspaces.prepare("run-h", str(target_repo), "main")
    h = workspaces.lock("run-h")
    with pytest.raises(WorkspaceError):
        workspaces.lock("run-h")
    h.close()
    workspaces.lock("run-h").close()


def test_patch_export_and_changed_files(workspaces, target_repo):
    m = workspaces.prepare("run-i", str(target_repo), "main")
    ws = workspaces.workspace_path("run-i")
    (ws / "src/greet.sh").write_text((ws / "src/greet.sh").read_text() + "# more\n")
    head = workspaces.commit("run-i", "x", "dev.writer", "s")
    assert workspaces.changed_files("run-i", m.baseline_commit, head) == [("M", "src/greet.sh")]
    patch = workspaces.export_patch("run-i", m.baseline_commit, head, "candidate.patch")
    assert "+# more" in patch.read_text()
