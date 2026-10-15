"""`magi` command line. Thin layer over `RunService`; no orchestration logic here."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from magi_system.models.run import PublicationMode
from magi_system.offline import DEMO_REQUEST, create_demo_repo, scripted_models
from magi_system.service import ResumeError, RunInputError, RunService, RunSettings

app = typer.Typer(add_completion=False, no_args_is_help=True, help="MAGI System — autonomous, policy-bounded software change runs.")

RunsRoot = Annotated[
    Path,
    typer.Option("--runs-root", envvar="MAGI_RUNS_ROOT", help="Directory holding run workspaces, artifacts and the run database."),
]
DEFAULT_RUNS_ROOT = Path(os.path.expanduser("~/.magi/runs"))


def _service(runs_root: Path, offline: bool) -> RunService:
    return RunService(runs_root, models=scripted_models() if offline else None)


def _print(data: dict) -> None:
    typer.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _exit_code(status: str) -> int:
    return 0 if status == "SUCCESS" else 2


@app.command()
def run(
    repo: Annotated[str, typer.Option(help="Local path or remote Git URL of the target repository.")],
    ref: Annotated[str, typer.Option(help="Commit, branch or tag to start from.")],
    request_file: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True, help="Markdown file with the change request.")],
    profile: Annotated[str, typer.Option(help="Authorized policy profile (defined in the platform policy).")] = "default",
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False, help="Run settings YAML (budgets, models). No secrets.")] = None,
    project: Annotated[str | None, typer.Option(help="Logical project identity (default: repository name).")] = None,
    include_uncommitted: Annotated[bool, typer.Option(help="Include uncommitted local changes as a recorded patch.")] = False,
    publication: Annotated[PublicationMode, typer.Option(help="Result publication mode (v1: local_candidate or patch).")] = PublicationMode.LOCAL_CANDIDATE,
    runner: Annotated[str | None, typer.Option(help="Execution runner override (must be allowed by the profile).")] = None,
    offline: Annotated[bool, typer.Option(help="Use the scripted fake model (demonstrates orchestration only).")] = False,
    runs_root: RunsRoot = DEFAULT_RUNS_ROOT,
) -> None:
    """Start a run on a target repository."""
    svc = _service(runs_root, offline)
    try:
        outcome = svc.start(
            repo=repo, ref=ref, request_text=request_file.read_text(), profile=profile, settings=RunSettings.load(config),
            project_identity=project, include_uncommitted=include_uncommitted, publication=publication, runner=runner,
        )
    except RunInputError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(outcome.model_dump(mode="json"))
    raise typer.Exit(_exit_code(outcome.status.value))


@app.command()
def resume(
    run_id: Annotated[str, typer.Option(help="Run to resume.")],
    offline: Annotated[bool, typer.Option(help="Use the scripted fake model.")] = False,
    runs_root: RunsRoot = DEFAULT_RUNS_ROOT,
) -> None:
    """Resume an interrupted run from its last checkpoint (refuses incompatible changes)."""
    try:
        outcome = _service(runs_root, offline).resume(run_id)
    except ResumeError as exc:
        typer.echo(f"error: cannot resume: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(outcome.model_dump(mode="json"))
    raise typer.Exit(_exit_code(outcome.status.value))


@app.command()
def status(run_id: Annotated[str, typer.Option()], runs_root: RunsRoot = DEFAULT_RUNS_ROOT) -> None:
    """Show the current state of a run."""
    try:
        _print(_service(runs_root, True).status(run_id))
    except KeyError as exc:
        typer.echo(f"error: unknown run {run_id}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def report(
    run_id: Annotated[str, typer.Option()],
    format: Annotated[str, typer.Option(help="json or md")] = "md",
    runs_root: RunsRoot = DEFAULT_RUNS_ROOT,
) -> None:
    """Print the run report (also saved under the run's artifacts/)."""
    from magi_system.service.app import render_report_md

    try:
        rep = _service(runs_root, True).report(run_id)
    except KeyError as exc:
        typer.echo(f"error: unknown run {run_id}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(render_report_md(rep) if format == "md" else json.dumps(rep, indent=2, sort_keys=True, default=str))


@app.command()
def demo(runs_root: Annotated[Path | None, typer.Option(help="Defaults to a new temporary directory.")] = None) -> None:
    """Offline end-to-end demo on a temporary target repository (distinct from magi-system)."""
    base = Path(tempfile.mkdtemp(prefix="magi-demo-"))
    repo = create_demo_repo(base / "greeter")
    svc = RunService(runs_root or base / "runs", models=scripted_models())
    typer.echo(f"target repository: {repo}")
    typer.echo(f"request: {DEMO_REQUEST.strip()}")
    outcome = svc.start(repo=str(repo), ref="main", request_text=DEMO_REQUEST, profile="offline-test")
    typer.echo(f"status: {outcome.status.value} — {outcome.diagnosis}")
    for c in outcome.gate_checks:
        typer.echo(f"  {c.verdict.value:<15} {c.name}")
    typer.echo(f"report: {svc.workspaces.artifacts_path(outcome.run_id) / 'report.md'}")
    typer.echo("note: scripted model — this demonstrates orchestration, not model quality.")
    raise typer.Exit(_exit_code(outcome.status.value))


if __name__ == "__main__":
    app()
