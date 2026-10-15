"""WorkspaceManager: filesystem and Git responsibilities for one run (brief §0.4).

It is NOT a security boundary for code execution; see `magi_system.execution`.

Layout under `runs_root/<run_id>/`:
  workspace/        isolated clone of the target, branch `magi/<run_id>`, no remotes
  artifacts/        patches, reports, evidence files
  inputs/           recorded run inputs (request, uncommitted patch if included)
  workspace.json    workspace manifest (redacted locator, commits)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from magi_system.models.knowledge import FileEntry
from magi_system.models.run import LocatorKind, RepoLocator, locator_has_credentials, redact_locator
from magi_system.workspace.git import GitError, git

STEP_TRAILER = "Magi-Step"
AGENT_TRAILER = "Magi-Agent"
RUN_TRAILER = "Magi-Run"


def control_repo_root() -> Path:
    """Root of the magi-system checkout this code runs from (best effort)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "magi_system").is_dir():
            return parent
    return here.parents[1]


class WorkspaceError(Exception):
    pass


class InvalidRevisionError(WorkspaceError):
    pass


class TargetInaccessibleError(WorkspaceError):
    pass


class UncommittedChangesError(WorkspaceError):
    pass


@dataclass
class WorkspaceManifest:
    run_id: str
    locator: str  # redacted
    locator_kind: str
    requested_ref: str
    resolved_commit: str
    baseline_commit: str  # resolved commit, or commit with the included uncommitted patch
    branch: str
    uncommitted_patch_sha256: str = ""
    created_at: float = field(default_factory=time.time)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class WorkspaceManager:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.resolve()
        if _is_within(self.runs_root, control_repo_root()):
            raise WorkspaceError("runs_root must not be inside the control repository")
        self.runs_root.mkdir(parents=True, exist_ok=True)

    # -- paths ----------------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or run_id.startswith("."):
            raise WorkspaceError(f"invalid run id: {run_id!r}")
        return self.runs_root / run_id

    def workspace_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "workspace"

    def artifacts_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "artifacts"

    # -- acquisition ----------------------------------------------------------------------

    @staticmethod
    def classify(repo: str) -> LocatorKind:
        if "://" in repo or (":" in repo and "@" in repo.split(":", 1)[0]):
            return LocatorKind.REMOTE
        return LocatorKind.LOCAL

    def _uncommitted_patch(self, source: Path) -> str:
        """Diff of tracked changes + untracked files, produced read-only from the source."""
        parts = [git("diff", "--binary", "HEAD", cwd=source).stdout]
        untracked = git("ls-files", "--others", "--exclude-standard", "-z", cwd=source).stdout
        for rel in filter(None, untracked.split("\0")):
            r = git("diff", "--binary", "--no-index", "--", os.devnull, rel, cwd=source, check=False)
            if r.returncode not in (0, 1):
                raise WorkspaceError(f"cannot diff untracked file {rel}: {r.stderr}")
            parts.append(r.stdout)
        return "".join(parts)

    def prepare(
        self,
        run_id: str,
        repo: str,
        ref: str,
        *,
        include_uncommitted: bool = False,
    ) -> WorkspaceManifest:
        """Acquire the target into an isolated clone. Never touches the source checkout."""
        kind = self.classify(repo)
        if kind is LocatorKind.REMOTE and locator_has_credentials(repo):
            raise TargetInaccessibleError(
                "remote URL embeds credentials; configure them in the infrastructure layer "
                "(git credential helper or SSH agent) and pass a URL without secrets"
            )
        run_dir = self.run_dir(run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise WorkspaceError(f"run directory already exists: {run_id}") from exc
        (run_dir / "artifacts").mkdir()
        (run_dir / "inputs").mkdir()
        ws = run_dir / "workspace"
        try:
            return self._prepare(run_id, repo, ref, kind, ws, include_uncommitted)
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    def _prepare(
        self, run_id: str, repo: str, ref: str, kind: LocatorKind, ws: Path, include_uncommitted: bool
    ) -> WorkspaceManifest:
        patch = ""
        if kind is LocatorKind.LOCAL:
            source = Path(repo).expanduser().resolve()
            if not source.is_dir():
                raise TargetInaccessibleError(f"target path does not exist: {source}")
            if _is_within(self.runs_root, source):
                raise WorkspaceError("runs_root must not be inside the target checkout")
            probe = git("rev-parse", "--show-toplevel", cwd=source, check=False)
            if probe.returncode != 0:
                raise TargetInaccessibleError(f"not a git repository: {source}")
            if Path(probe.stdout.strip()).resolve() != source:
                raise TargetInaccessibleError(f"path is not the repository root: {source}")
            dirty = git("status", "--porcelain", "--untracked-files=all", cwd=source).stdout.strip()
            if dirty and not include_uncommitted:
                raise UncommittedChangesError(
                    "target has uncommitted changes; commit them or pass --include-uncommitted"
                )
            if dirty:
                patch = self._uncommitted_patch(source)
            clone_src = str(source)
            clone_args = ["clone", "--no-checkout", "--no-hardlinks", "--quiet", clone_src, str(ws)]
        else:
            clone_args = ["clone", "--no-checkout", "--quiet", repo, str(ws)]
        try:
            git(*clone_args, allow_credentials=kind is LocatorKind.REMOTE)
        except GitError as exc:
            raise TargetInaccessibleError(f"cannot acquire target: {exc}") from exc

        commit = self._resolve(ws, ref)
        if patch:
            head = git("rev-parse", "HEAD", cwd=Path(repo).expanduser().resolve()).stdout.strip()
            if commit != head:
                raise WorkspaceError("--include-uncommitted requires --ref to resolve to the source HEAD")
        # Agents must not be able to push: drop remotes (also drops any URL from .git/config).
        for remote in filter(None, git("remote", cwd=ws).stdout.split()):
            git("remote", "remove", remote, cwd=ws)
        branch = f"magi/{run_id}"
        git("checkout", "--quiet", "-B", branch, commit, cwd=ws)
        baseline = commit
        patch_sha = ""
        if patch:
            patch_sha = hashlib.sha256(patch.encode()).hexdigest()
            (self.run_dir(run_id) / "inputs" / "uncommitted.patch").write_text(patch)
            git("apply", "--binary", "--index", "-", cwd=ws, input_text=patch)
            baseline = self.commit(run_id, "magi: include caller's uncommitted changes", "sys.platform", "baseline-patch")
        manifest = WorkspaceManifest(
            run_id=run_id,
            locator=redact_locator(repo if kind is LocatorKind.REMOTE else str(Path(repo).expanduser().resolve())),
            locator_kind=kind.value,
            requested_ref=ref,
            resolved_commit=commit,
            baseline_commit=baseline,
            branch=branch,
            uncommitted_patch_sha256=patch_sha,
        )
        (self.run_dir(run_id) / "workspace.json").write_text(json.dumps(asdict(manifest), indent=2))
        return manifest

    @staticmethod
    def _resolve(ws: Path, ref: str) -> str:
        if not ref or ref.startswith("-"):
            raise InvalidRevisionError(f"invalid revision: {ref!r}")
        for candidate in (ref, f"origin/{ref}", f"refs/tags/{ref}"):
            r = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{candidate}^{{commit}}", cwd=ws, check=False)
            if r.returncode == 0:
                return r.stdout.strip()
        raise InvalidRevisionError(f"revision not found in target: {ref!r}")

    def locator_model(self, manifest: WorkspaceManifest) -> RepoLocator:
        return RepoLocator(kind=LocatorKind(manifest.locator_kind), value=manifest.locator)

    def load_manifest(self, run_id: str) -> WorkspaceManifest:
        path = self.run_dir(run_id) / "workspace.json"
        if not path.is_file():
            raise WorkspaceError(f"workspace manifest missing for run {run_id}")
        return WorkspaceManifest(**json.loads(path.read_text()))

    # -- candidate operations -------------------------------------------------------------

    def _ws(self, run_id: str) -> Path:
        ws = self.workspace_path(run_id)
        if not (ws / ".git").is_dir():
            raise WorkspaceError(f"workspace missing for run {run_id}")
        return ws

    def head(self, run_id: str) -> str:
        return git("rev-parse", "HEAD", cwd=self._ws(run_id)).stdout.strip()

    def commit_exists(self, run_id: str, commit: str) -> bool:
        if not commit:
            return False
        r = git("cat-file", "-e", f"{commit}^{{commit}}", cwd=self._ws(run_id), check=False)
        return r.returncode == 0

    @staticmethod
    def step_tag(step_key: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", step_key)
        return f"refs/tags/magi-step/{safe}"

    def find_step_commit(self, run_id: str, step_key: str) -> str | None:
        """Commit recorded for `step_key` (idempotent resume). Tags survive resets."""
        r = git("rev-parse", "--verify", "--quiet", f"{self.step_tag(step_key)}^{{commit}}", cwd=self._ws(run_id), check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def is_dirty(self, run_id: str) -> bool:
        return bool(git("status", "--porcelain", "--untracked-files=all", cwd=self._ws(run_id)).stdout.strip())

    def commit(self, run_id: str, message: str, agent: str, step_key: str) -> str:
        """Commit all workspace changes. Idempotent per `step_key`. Returns HEAD if nothing changed."""
        ws = self._ws(run_id)
        existing = self.find_step_commit(run_id, step_key)
        if existing:
            return existing
        git("add", "-A", cwd=ws)
        if git("diff", "--cached", "--quiet", cwd=ws, check=False).returncode == 0:
            return self.head(run_id)
        body = f"{message}\n\n{STEP_TRAILER}: {step_key}\n{AGENT_TRAILER}: {agent}\n{RUN_TRAILER}: {run_id}\n"
        git("commit", "--quiet", "--no-verify", "-F", "-", cwd=ws, input_text=body)
        head = self.head(run_id)
        git("update-ref", self.step_tag(step_key), head, cwd=ws)
        return head

    def record_step(self, run_id: str, step_key: str, commit: str) -> None:
        """Record a no-change step so a resumed node does not redo it."""
        git("update-ref", self.step_tag(step_key), commit, cwd=self._ws(run_id))

    def restore(self, run_id: str, commit: str) -> None:
        """Reset the workspace to a recorded candidate, discarding partial writes."""
        ws = self._ws(run_id)
        if not self.commit_exists(run_id, commit):
            raise WorkspaceError(f"candidate {commit} not found in workspace of run {run_id}")
        git("reset", "--quiet", "--hard", commit, cwd=ws)
        git("clean", "-fdxq", cwd=ws)

    def changed_files(self, run_id: str, base: str, head: str) -> list[tuple[str, str]]:
        """(status, path) pairs between two commits; status in A/M/D."""
        if base == head:
            return []
        out = git("diff", "--name-status", "--no-renames", "-z", base, head, cwd=self._ws(run_id)).stdout
        items = out.split("\0")
        return [(items[i], items[i + 1]) for i in range(0, len(items) - 1, 2)]

    def diff(self, run_id: str, base: str, head: str) -> str:
        return git("diff", "--binary", base, head, cwd=self._ws(run_id)).stdout

    def export_patch(self, run_id: str, base: str, head: str, name: str) -> Path:
        out = self.artifacts_path(run_id) / name
        out.write_text(self.diff(run_id, base, head))
        return out

    def snapshot_files(self, run_id: str, commit: str) -> list[FileEntry]:
        out = git("ls-tree", "-r", "-l", "-z", commit, cwd=self._ws(run_id)).stdout
        entries = []
        for rec in filter(None, out.split("\0")):
            meta, _, path = rec.partition("\t")
            _mode, typ, blob, size = meta.split()
            if typ == "blob":
                entries.append(FileEntry(path=path, size=int(size) if size != "-" else 0, blob=blob))
        return entries

    def file_at(self, run_id: str, commit: str, path: str) -> str | None:
        r = git("show", f"{commit}:{path}", cwd=self._ws(run_id), check=False)
        return r.stdout if r.returncode == 0 else None

    # -- locking & retention --------------------------------------------------------------

    def lock(self, run_id: str):
        """Exclusive advisory lock: one process drives a run at a time."""
        handle = open(self.run_dir(run_id) / ".lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise WorkspaceError(f"run {run_id} is already being driven by another process") from exc
        return handle

    def cleanup(self, run_id: str, *, keep_artifacts: bool = True) -> None:
        run_dir = self.run_dir(run_id)
        shutil.rmtree(run_dir / "workspace", ignore_errors=True)
        if not keep_artifacts:
            shutil.rmtree(run_dir, ignore_errors=True)

    def prune(self, older_than_seconds: float, *, keep_artifacts: bool = True) -> list[str]:
        now, removed = time.time(), []
        for run_dir in self.runs_root.iterdir():
            manifest = run_dir / "workspace.json"
            if manifest.is_file() and now - manifest.stat().st_mtime > older_than_seconds:
                self.cleanup(run_dir.name, keep_artifacts=keep_artifacts)
                removed.append(run_dir.name)
        return removed
