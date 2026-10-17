# magi-system

```text
                     _______________________
                    /                       \
                   /      CASPER-MAGI 3      \
                  /   challenge assumptions   \
                  \___________________________/
                       //               \\
                      //    M  A  G  I   \\
   __________________//                   \\__________________
  /                  \     S Y S T E M     /                  \
 /  BALTHASAR-MAGI 2  \===================/  MELCHIOR-MAGI 1   \
 \   failure modes    /                   \   minimal change   /
  \__________________/                     \__________________/

          Three architects. Facts checked, not voted.
```

## Goal

Give magi-system a software change request and a Git repository, and it delivers the change
**autonomously**, with no human in the loop, while staying inside strict, code-enforced limits.
Every run ends in one of two ways:

- a **verified candidate**: a patch and a local branch that passed every mandatory check
  (build, tests, lint, acceptance, security, operations, documentation, final review);
- an **explicit failure state** with a diagnosis (failed verification, unresolved requirement,
  policy denied, infrastructure failure, budget exhausted).

It never pushes, merges or deploys. Those stay a separate, human-authorized step.

See it on a real target: [case study of the first SUCCESS](examples/run-20261015-205501-e0a70f/article.md)
(a Dev/SRE Toolbox added to a FastAPI app, 10/10 gates, $1.05, 19 minutes), and the same change
[hosted on Amazon Bedrock AgentCore](examples/run-20261017-155113-agentcore/article.md)
(10/10 gates, $1.35 of model cost, 26 minutes).

## Why three architects

Everything downstream starts from the architecture. Developers implement the plan, QA tests
against it, SRE and Security review it, the Tech Writer documents it. If the plan comes from a
single architect and that architect hallucinates (a function that does not exist, a library
capability the installed version lacks, a policy nobody wrote, an unstated business rule), the
error propagates: every later agent builds faithfully on a wrong foundation, and the result can
look consistent while being wrong.

magi-system is designed to **reduce** this risk, not to eliminate it:

- **Three independent analyses.** Melchior, Balthasar and Casper start from the same snapshot
  but work separately, with different primary perspectives, so a hallucination in one of them
  is less likely to be shared by all three.
- **Facts are checked, not voted on.** Every factual claim a plan relies on (file, symbol,
  dependency version, policy key, command) is verified against the real repository with tools.
  A plan built on a refuted claim, an unresolved critical unknown or a confirmed blocker is
  rejected, however many architects support it.
- **Cross-review.** Each architect scores the other two proposals with authors masked, and
  QA, SRE and Security review them before any code is written.
- **Verification after implementation.** Tests, acceptance checks and a final review by the
  three architects run on the actual change, so problems that slipped through design can still
  stop the run.

What it does **not** guarantee: the three architects can share the same mistake (same
training, same misleading context), different models do not remove that, and not every claim
can be checked mechanically. Operational guarantees and business assumptions stay unverified
and block a plan when critical, instead of being trusted. The goal is fewer and earlier caught
errors, plus an explicit failure instead of a confident wrong answer, not certainty.

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

On a laptop, keep the machine awake while a run is active (macOS: `caffeinate -i magi run ...`);
see [known limits](docs/limits.md).

Exit codes: `0` SUCCESS, `2` any other terminal status, `1` the run could not start.

Each run lives in `<runs-root>/<run-id>/`: `workspace/` (isolated clone, branch
`magi/<run-id>`, no remotes), `artifacts/` (`candidate.patch`, `report.md`, `report.json`,
per-agent patches), `inputs/` (request, uncommitted patch), `events.jsonl` (redacted
structured events). Checkpoints and run records are in `<runs-root>/magi.sqlite`.

## Configuration and secrets

Nothing environment-specific or secret lives in the code:

| What | Where |
|---|---|
| LLM API keys | Environment (`OPENROUTER_API_KEY`, primary; or `ANTHROPIC_API_KEY`). Never in config files, state, logs or reports. |
| Git credentials | Your Git credential helper / SSH agent, used only to clone. URLs with embedded credentials are rejected. |
| Langfuse | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` + `langfuse: true` in settings. |
| Budgets, models | Settings file passed with `--config` (`examples/settings.example.yaml`). |
| Target project description | Optional `.magi/config.yaml` in the target (`examples/magi-config.example.yaml`). |
| Runs location | `--runs-root` or `MAGI_RUNS_ROOT`. |
| Run store | `MAGI_STORE`: `sqlite` (default, `<runs-root>/magi.sqlite`) or `dynamodb` (hosted; see [docs/agentcore/](docs/agentcore/README.md)). |
| Platform policy | `src/magi_system/policy/platform_policy.yaml` (protected; versioned with the platform). |

Real providers: see [docs/providers.md](docs/providers.md).

## GitHub Actions

```yaml
- uses: lorenzogirardi/magi-system@v1
  with:
    request-file: request.md
    image: docker.io/library/python:3.12
  env:
    OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Uploads the verified patch and report as an artifact; never pushes. See
[docs/github-actions.md](docs/github-actions.md) for inputs, image choice and security rules.

## Hosted on AWS (Amazon Bedrock AgentCore)

An optional deployment runs magi-system on managed AWS services only (no EC2):
- AgentCore Runtime hosts the orchestrator.
- AgentCore Code Interpreter is the command sandbox.
- EFS holds the workspaces; DynamoDB holds the checkpoints.
- Models are still called through OpenRouter only.

One run is one Runtime session; the code is a bundle in S3 loaded at session start, so a code change
needs no image rebuild (`deploy/agentcore/push_code.py`). The deployment is Terraform
(`deploy/agentcore/terraform`): a dedicated VPC, a switchable NAT gateway (`deploy/agentcore/nat.sh`, the only
hourly-billed part), a CloudWatch dashboard with model and infrastructure cost, and the helpers to start a run,
fetch its patch and try it (`run_and_log.py`, `fetch-result.sh`, `try-candidate.sh`).

**Deployed and verified on a real target** (SUCCESS, case study above). See
[docs/agentcore/](docs/agentcore/README.md), with [deploy](docs/agentcore/deploy.md),
[observability](docs/agentcore/observability.md) and [limits and costs](docs/agentcore/limits-and-costs.md).

## Documentation

- [Architecture](docs/architecture.md): repositories, components, graph, decision protocol.
- [State, failures and resume](docs/state-and-failures.md): state model, terminal states, fix loops, budgets, resume rules.
- [Security model](docs/security.md): trust boundaries, what is and is not a sandbox.
- [Providers](docs/providers.md): configuring real LLMs.
- [GitHub Actions](docs/github-actions.md): composite action, example workflow, security rules.
- [AgentCore hosting](docs/agentcore/README.md): managed AWS deployment, request flow, sandbox, Terraform, code bundles,
  observability, costs and limits.
- [Known limits](docs/limits.md): what is not implemented.
- [Brief](docs/brief/): the normative specification this implements.
- [Case study: first SUCCESS on a real target](examples/run-20261015-205501-e0a70f/article.md)
  and [the twelve runs that led to it](examples/run-20261015-205501-e0a70f/training.md),
  with the request and the run artifacts.
- [Case study: the same change on Amazon Bedrock AgentCore](examples/run-20261017-155113-agentcore/article.md)
  and [operational lessons](examples/run-20261017-155113-agentcore/training.md), with the request, the full run state,
  the patch and console screenshots.
