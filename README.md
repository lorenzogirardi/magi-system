# magi-system

## Goal

Give magi-system a software change request and a Git repository, and it delivers the change
**autonomously**, with no human in the loop, while staying inside strict, code-enforced limits.
Every run ends in one of two ways:

- a **verified candidate**: a patch and a local branch that passed every mandatory check
  (build, tests, lint, acceptance, security, operations, documentation, final review);
- an **explicit failure state** with a diagnosis (failed verification, unresolved requirement,
  policy denied, infrastructure failure, budget exhausted).

It never pushes, merges or deploys. Those stay a separate, human-authorized step.

The core idea: a single LLM designing a change can be confidently wrong. magi-system asks
**three independent architects** to analyze the request and propose a plan, then lets code,
not a vote, decide which plan is acceptable. Every factual claim an architect makes (a file
exists, a function exists, a dependency version is available, a policy allows something) is
checked against the real repository with tools. A plan built on a false claim, an unresolved
critical unknown or a confirmed blocker is rejected, however many architects support it.

## The three architects

All three are complete enterprise architects: each one considers requirements, existing code,
data and APIs, security, reliability, cost, migration and rollback, testing and documentation.
They differ in where they look first, so their proposals and reviews catch different problems.

| Architect | Primary perspective | Typical question |
|---|---|---|
| **Melchior-Magi 1** | Evolution of the existing system: reuse, simplicity, the minimum change that works, compatibility. | "What is the smallest safe change that fits the code we already have?" |
| **Balthasar-Magi 2** | System behavior: data consistency, failure modes, dependencies, observability and recovery. | "What breaks, and how do we notice and recover when it does?" |
| **Casper-Magi 3** | Challenge: hidden assumptions, alternative designs, trust boundaries, unnecessary scope. | "What are we assuming, and is there a simpler or safer way?" |

How they work together:

1. Each architect analyzes the request **independently**, with read-only access to the
   repository and without seeing the others' work.
2. Each produces a complete proposal.
3. Each scores the **other two** proposals with authors masked (never its own).
4. QA, SRE and Security review the proposals before any code is written.
5. Tools verify the claims; code computes which proposals are admissible and ranks them.
6. After implementation, the same three roles review the final result in a fresh context.

Their perspectives are a starting point, not exclusive competences: Casper does not invent
objections to justify its role, and an objection without checkable evidence is never a veto.

## What happens in a run

```
request -> 3 architects (analysis, proposals, cross-review) -> plan selected by code
        -> DEV1 writes -> DEV2 pairs -> QA / SRE / Security verify
        -> Tech Writer documents -> 3 architects review -> deterministic gate
```

Problems found along the way are routed back automatically: a local bug to the developer who
introduced it, an architectural problem to the architects, a documentation gap to the Tech
Writer. Every loop has a budget.

> The names come from the MAGI System of *Neon Genesis Evangelion*: only the names and the
> plurality of perspectives. Agents behave as enterprise professionals, not characters.

## Quick start (offline, no API key)

```sh
uv sync
uv run magi demo          # temp target repo -> MAGI -> writer -> pair -> checks -> docs -> gate
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

- [Architecture](docs/architecture.md): repositories, components, graph, decision protocol.
- [State, failures and resume](docs/state-and-failures.md): state model, terminal states, fix loops, budgets, resume rules.
- [Security model](docs/security.md): trust boundaries, what is and is not a sandbox.
- [Providers](docs/providers.md): configuring real LLMs.
- [GitHub Actions](docs/github-actions.md): composite action, example workflow, security rules.
- [Known limits](docs/limits.md): what is not implemented.
- [Brief](docs/brief/): the normative specification this implements.
