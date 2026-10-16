# State, failures and resume

## State

The graph state (`engine/state.py`) is JSON-native: artifacts are dicts keyed by stable
id. Pydantic models validate them at node boundaries. Run-scoped dependencies (models,
runner, workspace, event log) travel in LangGraph's `Runtime` context and are never
checkpointed.

| Reducer | Used for | Why |
|---|---|---|
| `merge_by_id` | artifacts, stage results, gate results | Concurrent fan-out writes never collide; re-delivered writes are idempotent; `None` deletes a key (explicit invalidation). |
| `max_counters` | budgets, remediation and retry counters | Monotonic under concurrent and replayed writes. |
| `append_unique` | routing trail | Ordered audit trail without duplicates. |

Keys are scoped so results never mix: MAGI stages use `stage:r{round}:{agent}`;
verification, specialist and final-review results use the candidate commit
(`gate@<commit>`, `specialist:<commit12>:<agent>`).

Every artifact carries `meta`: `id`, `producer`, `round`, `phase`, `plan_version`,
`input_hash`, `evidence_refs`.

## Evidence and candidates

Every check result records `candidate_commit`. A fix produces a new commit; results for
the old commit stay in state (for audit) but are never read for the new one, so stale
evidence cannot be reused. The gate reads only results whose commit equals the final
candidate. In v1 every mandatory gate is re-run on the final candidate (documentation
commits included).

## Verdicts

`PASS`, `FAIL`, `UNKNOWN` (not executed / not demonstrable), `ERROR` (tool or
environment failure), `NOT_APPLICABLE` (only for gates in
`policy.not_applicable_allowed`, and only with a justification from the target config or
the SRE / Tech Writer).

Mandatory gates (`policy.mandatory_gates`): `project.build`, `project.test`,
`project.lint`, `qa.acceptance`, `security.review`, `sre.review`, `docs.updated`,
`magi.final_review`, `integrity.protected_paths`, `scope.write_scopes`.

`qa.acceptance` needs at least one executed acceptance command: QA review alone is
UNKNOWN. A project command that is missing or denied by policy makes its gate UNKNOWN.

## Terminal states

| Status | When |
|---|---|
| `SUCCESS` | Every mandatory gate PASS or allowed N/A on the final candidate, control plane unchanged. Means *verified change*, not merged or deployed. |
| `FAILED_VERIFICATION` | A mandatory gate not passed, fix attempts exhausted, or no admissible plan for factual reasons. |
| `UNRESOLVED_REQUIREMENT` | No traceable mandatory requirement, unresolved critical unknowns, or escalation with no architecture rounds left. |
| `POLICY_DENIED` | Target config tries to change authorization or is malformed; protected path changed; control plane modified during the run. |
| `INFRA_FAILURE` | Agent failures beyond technical retries, Git/filesystem errors, crash (resumable). |
| `BUDGET_EXHAUSTED` | Any budget dimension, remediation cycles, or the graph step limit. |

No state waits for a human.

## Agent tool loop limits

Agents work in bounded turns: each turn returns tool actions or a final answer, and the
whole transcript (with every tool result) is resent on the next turn. To keep it bounded:

| Limit | Value | Where | When reached |
|---|---|---|---|
| Turns per agent step | 12 | policy `limits.max_agent_tool_iterations` | `invalid_output` failure ("tool loop exceeded max iterations") |
| Actions per turn | 10 | policy `limits.max_agent_actions_per_turn` | extra actions are not run; each gets `error: not executed: at most 10 actions per turn` in the results |
| `read_file` size | 20000 bytes per call | `tools/toolbox.py` | result has `truncated: "file continues: call read_file with offset=N"`; pass `offset` to read the next page |
| `grep` matches | 200 | `tools/toolbox.py` | result has `truncated: "more matches exist: narrow the pattern or glob"` |

Each turn ends with `Turn i of N: k turn(s) left; at most 10 actions per turn.`; the last
turn says `last turn. Return the final answer now, with no actions.` Nothing is cut silently. Before these limits a real run reached 269 actions in one turn and
721k input tokens on a third turn.

## Invalid and truncated model output

Every agent call expects one JSON object matching its schema. If the output is not valid
JSON, fails the schema, or was cut off by the provider at `max_output_tokens`
(`finish_reason=length` / `stop_reason=max_tokens`), the call is retried once with the
error appended to the request (resending the identical request at temperature 0 would
return the identical output). Each rejected output emits `agent_invalid_output` with
`detail` and a redacted `excerpt` (first and last 300 characters; the tail shows truncation
and repetition loops). Tokens and cost of rejected
calls count toward the budget. A second rejection is an `invalid_output` agent failure,
handled by technical retries like any other agent failure.

## Fix loops (triage)

| Issue | Route |
|---|---|
| Failing check / confirmed code finding on DEV1's code | DEV1 → pair → all checks |
| `pair_bug` finding, or a check that passed on DEV1's candidate and fails after DEV2 changed code | DEV2 → all checks |
| Confirmed `architecture` finding or developer escalation | MAGI subgraph, new round and plan version, from the baseline |
| Confirmed `documentation` finding | Tech Writer |
| `ERROR` verdicts / `tooling` findings | Technical retry of the verification (separate counter) |
| Integrity violation | Terminal `POLICY_DENIED` |

Each fix has findings, an owner, and a counter (`max_fixes_per_finding`,
`max_remediation_cycles`).

## Budgets (`RunBudget`)

`max_architecture_rounds`, `max_fixes_per_finding`, `max_remediation_cycles`,
`max_technical_retries`, `max_duration_seconds`, `max_tool_calls`, `max_llm_calls`,
`max_tokens` (only when the provider reports usage), `max_cost_usd` (only when reported;
OpenRouter reports cost, the Anthropic adapter does not, so there call/token limits apply). Consumption is
in `budget_used` in state and in the report.

## Reproducibility record

Each run record stores: magi-system version and commit (or `MAGI_PLATFORM_REF` when not
running from a git checkout, e.g. inside the GitHub Action), whether `src/` had local
changes, the prompt manifest, policy version/profile/hash, the control-plane fingerprint
(content hash of all platform sources — the authoritative identity), effective agent
configuration (no secrets), target locator and commits, project config used (and what was
inferred), and the execution environment id (`local:...` or `container:<runtime>:<image>`).

## Resume

`magi resume --run-id X` checkpoints are per node: LangGraph `SqliteSaver` by default, or
`DynamoDBSaver` (`langgraph-checkpoint-aws`) with `MAGI_STORE=dynamodb`. Both use the same
strict serializer (no arbitrary class revival). On AgentCore, `resume` is an invocation that
may run in a new session; see [agentcore/api.md](agentcore/api.md). Resume
refuses if:

- prompt manifest (name@version#sha) differs from the one recorded;
- effective policy hash differs;
- control-plane fingerprint (all `magi_system` sources, prompts, policy) differs;
- the workspace is missing, or the recorded candidate / baseline commit is not in it.

Checkpoints do not restore files, processes or branches. Side effects are made
idempotent instead:

- each agent step commits under a step key recorded as a Git ref
  (`refs/tags/magi-step/<agent>:p<plan>:c<cycle>`); a re-run node reuses that commit and
  does not call the agent again;
- nodes that write start by restoring the workspace to their input commit, discarding
  partial writes;
- publication (patch export) is recorded with a dedup key in the `effects` table (SQLite),
  or as an `effect#<key>` item written with a conditional put (DynamoDB).

A finished run is not re-run by `resume`; the recorded outcome is returned.
