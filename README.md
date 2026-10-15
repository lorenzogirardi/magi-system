# magi-system

Autonomous, policy-bounded software change platform built on LangGraph. Three
independent architects — **Melchior-Magi 1**, **Balthasar-Magi 2**, **Casper-Magi 3** —
analyze a change request and propose plans; code (not a vote) verifies their claims with
tools, selects an admissible plan, and drives implementation (DEV1 writer → DEV2 pair),
QA/SRE/Security verification, documentation and a final review to a deterministic gate.

No human-in-the-loop, and no unlimited authority: a run ends in a verified local
candidate (patch + branch) or an explicit failure state. Push, merge and deployment are
never performed.

> The names come from the MAGI System of *Neon Genesis Evangelion*: only the names and
> the plurality of perspectives. Agents are enterprise professionals, not characters.

## Quick start (offline, no API key)

```sh
uv sync
uv run magi demo          # temp target repo → MAGI → writer → pair → checks → docs → gate
uv run pytest             # offline, deterministic test suite
```

The offline mode uses a scripted fake model. It demonstrates **orchestration behavior,
not the quality of real models**.

## Usage

```sh
magi run --repo /path/to/project --ref main --request-file request.md [--config settings.yaml]
magi run --repo https://git.example.org/team/project.git --ref v1.4.0 --request-file request.md
magi status --run-id <run-id>
magi resume --run-id <run-id>
magi report --run-id <run-id> [--format md|json]
```

| Option | Meaning |
|---|---|
| `--repo` | Local Git repository root or remote URL (no credentials in the URL). |
| `--ref` | Commit, branch or tag; resolved to one commit and recorded. |
| `--request-file` | The change request (Markdown). It is the mandate. |
| `--profile` | Authorized policy profile (`default`, `local-dev`, `offline-test`, `quorum-2`, `offline-test-quorum-2`). |
| `--config` | Run settings: budgets and per-agent models. See `examples/settings.example.yaml`. |
| `--include-uncommitted` | Include the caller's uncommitted changes as a recorded patch (rejected otherwise). |
| `--runner` | `container` (default, podman/docker; the image must be pulled beforehand) or `local` (only if the profile allows it; NOT a sandbox). |
| `--offline` | Use the scripted fake model. |
| `--runs-root` / `MAGI_RUNS_ROOT` | Where workspaces, artifacts and the run database live (default `~/.magi/runs`). |

Exit codes: `0` SUCCESS, `2` any other terminal status, `1` the run could not start.

Each run lives in `<runs-root>/<run-id>/`: `workspace/` (isolated clone, branch
`magi/<run-id>`, no remotes), `artifacts/` (`candidate.patch`, `report.md`, `report.json`,
per-agent patches), `inputs/` (request, uncommitted patch), `events.jsonl` (redacted
structured events). Checkpoints and run records are in `<runs-root>/magi.sqlite`.

## Configuration and secrets

Nothing environment-specific or secret lives in the code:

| What | Where |
|---|---|
| LLM API keys | Environment (`ANTHROPIC_API_KEY`). Never in config files, state, logs or reports. |
| Git credentials | Your Git credential helper / SSH agent, used only to clone. URLs with embedded credentials are rejected. |
| Langfuse | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` + `langfuse: true` in settings. |
| Budgets, models | Settings file passed with `--config` (`examples/settings.example.yaml`). |
| Target project description | Optional `.magi/config.yaml` in the target (`examples/magi-config.example.yaml`). |
| Runs location | `--runs-root` or `MAGI_RUNS_ROOT`. |
| Platform policy | `src/magi_system/policy/platform_policy.yaml` (protected; versioned with the platform). |

Real providers: see [docs/providers.md](docs/providers.md).

## GitHub Actions

```yaml
- uses: lorenzogirardi/magi-system@v1
  with:
    request-file: request.md
    image: docker.io/library/python:3.12
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Uploads the verified patch and report as an artifact; never pushes. See
[docs/github-actions.md](docs/github-actions.md) for inputs, image choice and security rules.

## Documentation

- [Architecture](docs/architecture.md) — repositories, components, graph, decision protocol.
- [State, failures and resume](docs/state-and-failures.md) — state model, terminal states, fix loops, budgets, resume rules.
- [Security model](docs/security.md) — trust boundaries, what is and is not a sandbox.
- [Providers](docs/providers.md) — configuring real LLMs.
- [GitHub Actions](docs/github-actions.md) — composite action, example workflow, security rules.
- [Known limits](docs/limits.md) — what is not implemented.
- [Brief](docs/brief/) — the normative specification this implements.
