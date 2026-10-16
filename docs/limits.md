# Known limits

- **Offline tests and the demo use a scripted model.** They demonstrate orchestration
  (routing, gates, budgets, resume, isolation), not the quality of real models. No run
  against a real LLM has been executed during development.
- **Container runner**: the real-container unit test runs when
  `docker.io/library/debian:bookworm-slim` is present (CI pulls it; tests never pull).
  Integration tests use the LocalRunner; one full offline run through podman was verified
  manually (`python:3.12` image). The default `debian:bookworm-slim` image has no `make`:
  targets must name an image with their toolchain. The container has no network, so
  dependencies must be in the image or the repository.
- **GitHub Action**: its run step was exercised locally with simulated `GITHUB_*`
  variables; the workflow itself has not run on GitHub yet. It never pushes or opens PRs.
- **Anthropic adapter and Langfuse mirror** are not exercised by tests (network). The
  OpenRouter adapter is unit-tested with a mocked HTTP layer only.
- **Publication**: only a local candidate branch and an exported patch. Push, pull
  request, merge and deployment are not implemented; the report always states
  "integrated: no, deployed: no".
- **Services for tests** (`services` in `.magi/config.yaml`) are parsed but not started.
- **Proposal merging** (combining parts of proposals into a new versioned proposal) is
  not implemented; revisions create new proposal versions per architect.
- **Text claims** (`text_present`) match the pattern literally first, then as a regex: a
  quoted code line with `(`, `[` or `.` is not refuted by regex metacharacters.
- **Symbol verification** is regex-based (definitions in common languages and shell
  functions / Makefile targets), not a real parser.
- **Package capability checks** read `package.json`, `pyproject.toml` and `requirements.txt` only
  (spec `name`, `name>=ver` or `name==ver`, in `target` or, with a manifest as `target`, in
  `pattern`), and
  compare declared minimum versions, not installed versions.
- **Agent timeouts** abandon the provider call thread; the provider SDK timeout is also
  set so the call ends on its own.
- **Concurrency**: one process drives a run (file lock). Different runs can run in
  parallel processes; SQLite is shared with WAL.
- **Retention**: `WorkspaceManager.cleanup/prune` exist but no CLI command exposes them yet.
- **Laptop sleep.** On macOS, timeouts use a clock that stops while the system sleeps,
  and open connections are reset on wake. A run left unattended can hang until wake and
  then lose its in-flight model calls (reported as retryable `provider_error`). Keep the
  machine awake for the duration of the run: `caffeinate -i uv run magi run ...`.
- **AgentCore hosting is not deployed.** Under `deploy/agentcore/` there are code, image,
  Terraform and docs, tested offline only:
  - The DynamoDB store is tested under moto.
  - The Code Interpreter runner is tested with fake clients.
  - The entrypoint is tested directly and through a local container smoke test.
  - Terraform passes `validate` only, with no `plan` or `apply`.

  Items still to prove on AWS: the Code Interpreter response shape, the sandbox toolchain,
  and git/flock on EFS. The [spike checklist](agentcore/testing.md#spike-checklist-before-the-first-real-run)
  lists every item. A sandbox command is capped at 840 s. Hosted runs clone public
  repositories only, since there is no Git credential helper yet.
