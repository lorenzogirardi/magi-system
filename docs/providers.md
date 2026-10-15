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

Per-agent options (OpenRouter only; `anthropic` rejects them):

| Setting | Sent as | Example | Notes |
|---|---|---|---|
| `routing` | `provider` | `{sort: throughput}`, `{order: [together], allow_fallbacks: false}` | `require_parameters: true` is always added. |
| `reasoning` | `reasoning` | `{effort: low}`, `{enabled: false}`, `{max_tokens: 2000}` | Omit to use the model default. |

Measured on real pytbak MAGI requests with `deepseek/deepseek-v4.1-flash` (2026-10-15):
the endpoint dominates latency, not reasoning. On a 30k-token tool-loop turn default routing
took 94 s on average (Morph/OpenInference, up to 193 s), `sort: throughput` 2–8 s (mostly
Together). Reasoning tokens were at most ~1.7k per call, so `effort: low` / `enabled: false`
changed little. Some calls degenerated into ~30k tokens of repetitive output: keep
`max_output_tokens` bounded (e.g. 32000) so a runaway call ends quickly as `truncated`.

- Endpoint: `OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`); calls
  `POST {base}/chat/completions` with `usage: {include: true}`, so every response carries
  `prompt_tokens`, `completion_tokens` and `cost` (USD), which feed the run budget.
- Requests use schema-constrained output: `response_format: {type: json_schema, json_schema:
  {name, strict: true, schema}}` with the agent's output schema, and
  `provider: {require_parameters: true}`, so OpenRouter routes only to endpoints that
  enforce it. Without this DeepSeek v4.x answered in its native tool-call markup
  (`<｜DSML｜ calls>…`) instead of JSON; plain JSON mode (`json_object`) was not enough,
  because some endpoints accept but do not enforce it. Consequence: the model must have at
  least one endpoint supporting `response_format`/`structured_outputs`, otherwise the call
  fails with a `provider_error` (HTTP 404 "no endpoints"), and routing may pick a pricier
  endpoint than the cheapest one.
- The JSON schema is appended to the prompt (same as the Anthropic adapter); output is
  parsed and validated by the platform, invalid output is a typed `invalid_output` failure.
- `finish_reason: length` marks the response as truncated; the caller retries it with
  feedback (see [state-and-failures](state-and-failures.md#invalid-and-truncated-model-output)).
  Reasoning models (e.g. DeepSeek v4.x) count reasoning tokens in the output limit: size
  `max_output_tokens` generously (the first pytbak run hit 16000 on analysis turns).
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
