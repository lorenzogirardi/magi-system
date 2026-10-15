# Running MAGI in GitHub Actions

magi-system ships a composite action (`action.yml` at the repository root). A target
repository calls it from its own workflow; the run's outcome is a verified patch uploaded
as an artifact. **The action never pushes, opens pull requests, merges or deploys.** If you
want a pull request, add a separate, explicitly authorized step after reviewing the patch.

## Example consumer workflow

```yaml
# .github/workflows/magi.yml in the TARGET repository
name: MAGI change
on:
  workflow_dispatch:
    inputs:
      request:
        description: Change request
        required: true

permissions:
  contents: read

jobs:
  magi:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false   # MAGI never needs to push
          fetch-depth: 0               # full history so any --ref resolves
      - name: Write the request
        env:
          REQUEST: ${{ inputs.request }}
        run: printf '%s\n' "$REQUEST" > "$RUNNER_TEMP/request.md"
      - id: magi
        uses: lorenzogirardi/magi-system@v1   # pin a tag or commit of magi-system
        with:
          request-file: ${{ runner.temp }}/request.md
          image: docker.io/library/python:3.12   # must have the tools your build/test needs
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      - run: echo "status=${{ steps.magi.outputs.status }}"
```

The job summary shows the run report. The artifact `magi-<run-id>` contains
`candidate.patch`, `report.md`, `report.json`, per-agent patches, `events.jsonl` and the
outcome JSON. Apply it locally with `git apply candidate.patch`.

## Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `request-file` | — (required) | Markdown change request. |
| `repo-path` | `.` | Target repository root (the checkout). |
| `ref` | `${{ github.sha }}` | Start revision. |
| `profile` | `default` | Policy profile; `default` runs target commands in a container. |
| `config` | — | Run settings YAML (budgets, models). No secrets. |
| `image` | `docker.io/library/debian:bookworm-slim` | Image pulled before the run; must be allowlisted and match `container_image` in `.magi/config.yaml` if set. |
| `offline` | `false` | Scripted fake model (no API key; orchestration check only). |

Outputs: `status`, `run-id`, `patch` (path, empty unless SUCCESS). The step fails for any
status other than SUCCESS.

## Choosing the image

Target commands (build/test/lint, acceptance commands) run in the image with
`--network=none`. `debian:bookworm-slim` has `sh` but **no `make`**; pick an allowlisted
image (prefix `docker.io/library/`) that contains your toolchain, e.g. `python:3.12`,
`node:22`, `golang:1.23`, and set the same value as `container_image` in the target's
`.magi/config.yaml`. Dependencies must already be in the image or the repository: the
container has no network.

## Security rules

- **Trusted triggers only.** The request is the mandate. Use `workflow_dispatch`, or
  events from maintainers. Never feed an untrusted issue or PR body as the request without
  review.
- **Never `pull_request_target` with secrets.** It runs with secrets on code you did not
  review.
- **Keep the `default` profile in CI.** `local-dev` / `offline-test` profiles use the
  LocalRunner, which executes target code on the runner host: it can read the parent
  process environment via `/proc`, including `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`. Only the container runner
  isolates target code from secrets.
- `persist-credentials: false` on checkout: MAGI drops remotes in its workspace anyway, but
  there is no reason to leave a token in `.git/config`.
- Pin the action to a tag or commit SHA.

## CI for magi-system itself

`.github/workflows/ci.yml` runs ruff and pytest (with `debian:bookworm-slim` pulled so the
real ContainerRunner test runs), and `action-selftest`, which runs the action offline on
a generated demo repository and checks for SUCCESS and the expected patch.
