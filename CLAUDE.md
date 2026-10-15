# CLAUDE.md — magi-system

Autonomous agentic software-development platform (LangGraph) inspired by the MAGI System.
This repo is the **control repository**: orchestration, agents, prompts, policies, gates,
adapters, workspace management, persistence, CLI. It does NOT contain target project code.

## Sources of truth

- Full spec: `docs/brief/00-multi-repo.md` (sections 0.x) and
  `docs/brief/01-platform.md` (sections 1–15). Read both before implementing.
- Progress and plan: `STATUS.md`. Update it at the end of every phase/session.

## Stack and commands

- Python 3.13 via uv venv (system python is 3.14 — don't use it directly).
- Package manager `uv`; layout `src/magi_system/`; build backend `uv_build`.
- Deps: langgraph 1.2.15, langgraph-checkpoint-sqlite 3.1.2, pydantic 2.14, pyyaml,
  typer. Dev: pytest, pytest-timeout, ruff. Optional extras: `anthropic`, `langfuse`.
- `uv sync` — install
- `uv run pytest` — tests (offline, deterministic)
- `uv run magi demo` — offline end-to-end demo on a temp target repo
- `uv run magi run|resume|status|report ...` — CLI (see README)
- `uv run ruff check src tests` — lint
- Container runtime available: podman 5.8.2 (ExecutionRunner container backend). Local
  images used in testing: `debian:bookworm-slim` (no make), `python:3.12` (has make).
- GitHub Action: `action.yml` (composite); CI: `.github/workflows/ci.yml`.

## Verified LangGraph 1.2.15 facts

- `StateGraph(state_schema, context_schema=None, *, input_schema, output_schema)`.
- `add_node(..., retry_policy=, timeout=, error_handler=, destinations=, defer=)`.
- `langgraph.types`: `Send`, `Command`, `RetryPolicy`, `TimeoutPolicy`, `Overwrite`.
- `SqliteSaver(sqlite3.Connection)`; `setup()`, `list`, `get_tuple`, `delete_thread`.
- Compiled subgraph can be added as a node; `Send` fan-out + `Annotated` reducer works.
- Resume after node exception: `app.invoke(None, cfg)` re-runs only the failed node;
  completed fan-out writes are kept.
- Pydantic models in state deserialize from checkpoints but emit a warning for
  unregistered types ("will be blocked in a future version").
- Pitfall: a compiled subgraph sharing an `operator.add` key with the parent re-emits
  the parent's list and duplicates entries. Use idempotent merge-by-id reducers.
- `Runtime[Ctx]` with `context_schema=` injects non-checkpointed deps into nodes and
  subgraphs (`app.invoke(..., context=Ctx(...))`).
- DECISION: graph state is JSON-native (dicts keyed by artifact id, merged by id);
  Pydantic validation at node boundaries. Deps (model adapter, runner, workspace,
  event log) travel in Runtime context, never in state.
- Nodes get `runtime` injected by parameter NAME via `inspect.signature`: wrappers must
  not set `__wrapped__` (no `functools.wraps`), or injection silently breaks.
- After a crash inside a subgraph, the newest values are in the subgraph checkpoint:
  read them with `get_state(cfg, subgraphs=True)` and descend `tasks[0].state`.
- Always re-check signatures with `inspect.signature` before using a new API.

## Engineering principles (user directives)

- No over-engineering. Build what the goal needs, nothing speculative.
- Don't introduce layers, abstractions or indirection unless they are needed now.
  Prefer a plain function or module over a new class/protocol/package.
- Start from the goal: what we want to achieve and how to verify it. Then the smallest
  design that gets there.
- Always document everything: every feature, config option, command and decision goes in
  the docs (README / `docs/`) in the same change, and STATUS.md is kept current.
- Secrets and environment-specific configuration live outside the code: environment
  variables or external config files (an example file without values is committed). Never
  hardcode secrets, endpoints, tokens or machine-specific paths in source, prompts, tests
  or docs.

## Working rules

- Write everything in English (files, code, docs, replies), even though the user writes
  in Italian.
- No placeholder returning PASS: a check not executed is UNKNOWN or ERROR.
- During development never start real runs that modify external repos, push/deploy, or
  consume significant LLM budget. Fake model/tools only.
- Tests and demo use temporary target repos outside magi-system.
- Never change git config (local or global). Package metadata author email: `l@k8s.it`.
- No commit/push unless explicitly asked.
