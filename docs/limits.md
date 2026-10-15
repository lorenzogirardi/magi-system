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
- **Symbol verification** is regex-based (definitions in common languages and shell
  functions / Makefile targets), not a real parser.
- **Package capability checks** read `package.json` and `pyproject.toml` only, and
  compare declared minimum versions, not installed versions.
- **Agent timeouts** abandon the provider call thread; the provider SDK timeout is also
  set so the call ends on its own.
- **Concurrency**: one process drives a run (file lock). Different runs can run in
  parallel processes; SQLite is shared with WAL.
- **Retention**: `WorkspaceManager.cleanup/prune` exist but no CLI command exposes them yet.
