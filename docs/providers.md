# Configuring real LLM providers

Offline mode needs nothing. For real runs:

1. Install the provider extra: `uv sync --extra anthropic`.
2. Export the key in your environment (never in files under version control):
   `export ANTHROPIC_API_KEY=...`
3. Copy `examples/settings.example.yaml`, set models per agent, and pass it:
   `magi run --repo ... --ref ... --request-file request.md --config settings.yaml`.

Per-agent settings (`provider`, `model`, `temperature`, `max_output_tokens`,
`timeout_seconds`) are keyed by internal id: `magi.melchior`, `magi.balthasar`,
`magi.casper`, `dev.writer`, `dev.pair`, `spec.qa`, `spec.sre`, `spec.security`,
`doc.tech_writer`. Using different models per MAGI is allowed; it does not remove
common-mode errors — tool verification does.

The effective agent configuration (no secrets) is recorded in the run record.

## Adding a provider

Implement `ModelAdapter` (`llm/base.py`: `complete(request, *, timeout) -> ModelResponse`)
and register it in `llm/providers.py::make_adapter`. Report `input_tokens` /
`output_tokens` / `cost_usd` only if the provider returns them; otherwise leave `None`
(budgets then rely on call limits).

## Tracing

`uv sync --extra langfuse`, set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_HOST`, and `langfuse: true` in the settings file. Events are redacted before
they are sent. Tests never need Langfuse.

## Before a first real run

Real runs execute target commands. With the `default` profile they run in a container:
pull an allowlisted image first (e.g. `podman pull docker.io/library/debian:bookworm-slim`)
or set `container_image` in the target's `.magi/config.yaml`.
