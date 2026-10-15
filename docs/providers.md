# Configuring real LLM providers

Offline mode needs nothing. For real runs the primary provider is **OpenRouter**; direct
Anthropic is also available.

| Provider | `provider:` | Key (environment) | Extra | Usage / cost reported |
|---|---|---|---|---|
| OpenRouter (primary) | `openrouter` | `OPENROUTER_API_KEY` | none (stdlib HTTP) | tokens and cost (`max_cost_usd` enforced) |
| Anthropic (direct) | `anthropic` | `ANTHROPIC_API_KEY` | `uv sync --extra anthropic` | tokens only |

1. Export the key in your environment (never in files under version control):
   `export OPENROUTER_API_KEY=...`
2. Copy `examples/settings.example.yaml`, set the model (any OpenRouter model id, e.g.
   `deepseek/deepseek-v4.1-flash`) per agent or in `default_agent`, and pass it:
   `magi run --repo ... --ref ... --request-file request.md --config settings.yaml`.

### OpenRouter details

- Endpoint: `OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`); calls
  `POST {base}/chat/completions` with `usage: {include: true}`, so every response carries
  `prompt_tokens`, `completion_tokens` and `cost` (USD), which feed the run budget.
- The JSON schema is appended to the prompt (same as the Anthropic adapter); output is
  parsed and validated by the platform, invalid output is a typed `invalid_output` failure.
- HTTP errors, an `error` body or no `choices` → `provider_error`; socket timeout →
  `timeout`. Both are retried within `max_technical_retries`.

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
