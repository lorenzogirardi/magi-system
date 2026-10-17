# STATUS — magi-system

Last updated: 2026-10-16 (session 5, AgentCore hosting: implemented and tested offline, not deployed).

## State

v1 is implemented and runs end-to-end offline. `uv run magi demo` → SUCCESS on a
temporary greeter repo (POSIX sh/Makefile stack, distinct from magi-system).
`uv run pytest` → **208 passed, 1 skipped** (~65 s; the real-container test runs when
`debian:bookworm-slim` is pulled, otherwise it is skipped). `uv run ruff check src tests` clean.
Nothing committed yet.

Revision done (plan approved by the user): ContainerRunner fixes (runtime CLI env,
`image inspect`, docker `--user`, missing-image preflight), `MAGI_PLATFORM_REF` fallback,
composite GitHub Action + CI workflow, dead-code removal. Full offline run through the real
podman ContainerRunner verified manually (`python:3.12`). The action's run step was executed
locally with simulated `GITHUB_*` vars (success, bad ref, POLICY_DENIED); **the workflows
have not run on GitHub yet** (needs a push).

Session 4: OpenRouter is the primary LLM provider (`provider: openrouter`,
`OPENROUTER_API_KEY`, stdlib HTTP, usage + cost reported); Anthropic direct kept as secondary.
User directive: Anthropic is never used directly. Preparing the first real local run on the
pytbak FastAPI repo (scratch clone + `.magi/config.yaml`, docker image with deps, DeepSeek
v4.1 flash via OpenRouter).

First real run (`run-20261015-181700-caaf4d`, stopped by the user, $0.057): all three MAGI
analyses failed with invalid output: (1) truncation at `max_output_tokens: 16000`
(reasoning tokens count), not recognised as such; (2) `Extra data` (trailing content after
the JSON object); (3) retry resent the identical request, so it returned the identical
output. Fixed: `ModelResponse.truncated` from `finish_reason`/`stop_reason`, retry with the
error fed back, `excerpt` in `agent_invalid_output`. Parser stays strict on trailing data.
Run 2 (`run-20261015-182545-d8cd84`, stopped): the new excerpt showed the real cause:
DeepSeek answered in its native tool-call markup (DSML) instead of JSON, even after
feedback. JSON mode (`json_object`) was not enough (run 3, `run-20261015-182809-325f16`):
endpoint "Relace" accepts but does not enforce it; identical requests alternated JSON/DSML.
Fixed: `response_format: json_schema` (strict, agent schema) + `provider.require_parameters`;
3/3 valid on the captured real request (routed to Together).
Run 4 (`run-20261015-183419-8e52e8`): MAGI analysis worked (Casper done cleanly) but calls
were slow (minutes). Measured with a probe harness: endpoint dominates latency
(`sort: throughput` 2–8 s vs 94 s average), reasoning tokens small, some calls degenerate
into ~30k repetitive tokens. Added per-agent `routing` / `reasoning` (OpenRouter passthrough);
settings use `routing: {sort: throughput}`, `max_output_tokens: 32000`.
Open issue: during run 4 every run directory under `~/.magi/runs/` was deleted by something
outside magi-system (no magi code path removes completed runs); run 4 was stopped.
Run 5 (`~/magi-runs`, `run-20261015-184726-8f77ae`, stopped): fast and no invalid output
(Casper done in 1m46s), but context exploded (269 actions in one turn, 721k input tokens).
Added: max 10 actions per turn (policy), `read_file` 20 KB pages with `offset`, `grep` 200
matches, all with explicit truncation markers.
Run 6 (`run-20261015-185159-b080dd`, $0.16, 8 min): all three MAGI analyses completed
(Casper after two technical retries: truncation, then 12 turns without a final answer).
Ended BUDGET_EXHAUSTED (tool_calls 500) in proposals, then POLICY_DENIED because magi-system
code was edited during the run (control-plane integrity check working as designed; never
edit magi-system while a run is active). Also all 16 requirements were rejected: quotes
translated to English from the Italian request. Fixed: analysis prompt v2 (quotes verbatim
in the request's language), turn budget note in the tool loop, excerpt head+tail,
budgets in settings 3000 tool calls / 600 LLM calls.
Run 7 (`run-20261015-190205-1cbf27`): Casper analysis OK in 1m41s; Melchior and Balthasar
hung 16 min then `Connection reset by peer` → INFRA_FAILURE. Root cause (pmset log): the Mac
slept 19:02:40–19:18:48; monotonic timeouts do not advance in sleep and sockets reset on
wake. Fix: run under `caffeinate -i`; adapter maps connection errors to retryable
`provider_error`.
Run 8 (`run-20261015-194904-6c86f4`, under caffeinate): first run through analysis (1.5 min),
121 requirements accepted (Italian verbatim quotes), 3 proposals, cross-review and QA/SRE/
Security/Tech Writer reviews; then no proposal admissible. Cause: verifier bug — `text_present`
patterns were matched only as regex, so literal code quotes with `(`/`[`/`.` were refuted
(76 refuted, 58 of them false). Fixed: literal match first, then regex; `requirements.txt`
supported in package checks. Replaying run 8's 338 claims: 18 refuted, all genuine.
Run 9 (`run-20261015-195700-0efdc1`, $0.80, 15 min): plan selected (Balthasar, score 4.32);
DEV1 wrote 20 files (130/131 tests), DEV2 fixed the failing test; 8/10 mandatory gates PASS
on `ea193e7` (build, test, lint, 14 QA scenarios, security, SRE, protected paths, scopes).
Stopped BUDGET_EXHAUSTED (tokens 4M) in the Tech Writer step; docs.updated and
magi.final_review not reached. Observed temperature-0 repetition loops (SRE review ~150k
chars) caught by the 32k cap + retry. Settings: max_tokens 30M (cost cap $5 is the guard).
Run 10 (`run-20261015-201806-1570b9`, $1.02, 23 min): Casper's plan selected; DEV1 green at
first try; all checks + specialists PASS; Tech Writer done; checks re-run on the docs commit;
`magi.final_review` FAIL with a real confirmed bug (k8s summary counts computed before
findings truncation); routed back to DEV1, which fixed it with a regression test (all green).
Then crashed in DEV2's pair step: `run_command` with unbalanced quotes made `shlex.split`
raise ValueError, uncaught → INFRA_FAILURE. Fixed: unparseable commands are denied by the
policy engine (agent gets an error). Resume not possible after a code change (by design).
Run 11 (`run-20261015-204713-5e4a56`, stopped in round 1): all three proposals inadmissible
because package claims written as target=`requirements.txt`, pattern=`fastapi==0.143.1`
were refuted (target read as package name; `==` unsupported). Fixed: manifest target uses the
pattern as spec, `==` pins and extras supported. Replay of run 11 claims: 43 → 9 refuted,
all genuine.

**Run 12 (`run-20261015-205501-e0a70f`): SUCCESS — first verified change on a real target.**
$1.05, 19 min, 105 LLM calls, 5.3M tokens. Melchior's plan selected in round 0; DEV1 wrote
21 files (145/146 tests; one wrong expectation), DEV2 corrected the test with a justified
fix (verified: PyYAML yields 2 documents for `---\n---\n<doc>`); all 10 mandatory gates PASS
on `5cce890`. Independent check: `candidate.patch` applies cleanly to the original repo,
146 passed / 25 skipped in the container, API smoke via TestClient (401 without auth, catalog,
k8s findings, json.validate negative outcome with HTTP 200, 404 unknown tool, 422 malformed,
existing endpoints unaffected). Artifacts: `~/magi-runs/run-20261015-205501-e0a70f/artifacts/`.
Written up in `examples/run-20261015-205501-e0a70f/` (`article.md`: run 12 and functional
requirements; `training.md`: all twelve runs and the non-functional fixes; English
`request.md`; sanitized artifacts).

Open items from the real runs:
- `policy_exists` claims on a path (`.github/workflows/**`) are refuted; the model means
  "is a protected path". Recurs in every run; not blocking in run 12.
- Reviewers (specialists, final review) receive only the last 60,000 characters of the diff
  (`diff[-60000:]` in `engine/nodes_impl.py` / `nodes_verify.py`); run 12's diff was ~99k, so
  core files were not visible to them.
- Finding verification can wrongly refute a correct finding: run 12's "`nginx:` with an empty
  tag is not flagged" was refuted, but the code does not flag it.
- Temperature-0 repetition loops (reviews ~150k chars) are cut by the 32k cap and retried;
  they cost time/tokens. Not addressed (a penalty could also break JSON; measure first).
- `~/.magi/runs` contents disappeared during run 4 (cause unknown, outside magi-system);
  runs since use `--runs-root ~/magi-runs` with no recurrence.

## Session 5 — Amazon Bedrock AgentCore hosting (offline only)

Goal (user): run magi-system hosted on AWS with managed services only (no EC2). Models stay on
OpenRouter (never Bedrock models). Container images must not be Alpine (Debian slim is fine).
Reviewed first: Dify, rejected because it would replace the engine with a DSL and lose the
guarantees. Docs: `docs/agentcore/`.

Done, all offline (no AWS calls, no real LLM):
- `MAGI_STORE=dynamodb`: `DynamoRunStore` (runs/effects table) + `DynamoDBSaver`
  (`langgraph-checkpoint-aws` 1.2.5) with the same strict serializer, optional S3 offload and TTL.
  Tested under moto, including a full run and a crash/resume.
- `CodeInterpreterRunner` (`agentcore` profile, the only runner it allows):
  - per-run EFS access point and Code Interpreter session
  - `executeCommand` with `env -i`, timeout (840 s cap) and quoted argv
  - policy checked before sending
  - missing exit code = not PASS
  - `close()` releases the session and the access point
  - `RunService` now does a generic `available()` preflight and calls `runner.close()`.
- `magi-agentcore` entrypoint (`interfaces/agentcore.py`):
  - start/resume as AgentCore async tasks (`/ping` HealthyBusy), status, report (patch inline)
  - one run per session
  - `MAGI_ALLOWED_PROFILES` (default `agentcore`) and `MAGI_ALLOWED_PROVIDERS` (default `openrouter`)
  - OpenRouter key from Secrets Manager
  - events on stdout (`MAGI_EVENTS_STDOUT`).
- `deploy/agentcore/Dockerfile`: `python:3.13-slim`, arm64, uid 1000. `deploy/agentcore/smoke.sh`:
  podman build + real HTTP contract, start → HealthyBusy → SUCCESS → Healthy → report. **Passed.**
- `deploy/agentcore/terraform/`:
  - VPC `vpc-lab-ai-001` (`vpc_name` variable; private subnets with NAT for the runtime, isolated
    subnets for the sandbox). Target account/Region: <aws-account-id> / `eu-north-1` (AgentCore APIs answer).
    `nat_enabled` + `deploy/agentcore/nat.sh stop|start|status` switch off the only hourly cost (NAT + EIP).
    Installed Terraform is 1.5.7, the module needs >= 1.9: upgrade before validate/plan.
  - EFS, DynamoDB, S3, ECR, Secrets Manager
  - least-privilege IAM, with no Bedrock model access
  - `aws_bedrockagentcore_agent_runtime` + `aws_bedrockagentcore_code_interpreter` (provider aws 6.69.1)
  - `fmt -check` and `validate` pass. **Deployed 2026-10-16** in `eu-north-1` (VPC, NAT, ECR image
    `magi-agentcore:1ed5c87`, EFS, DynamoDB, S3, IAM, Code Interpreter, Runtime `READY`; OpenRouter key set).
    NAT is billed hourly: stop it with `deploy/agentcore/nat.sh stop` when idle.
  - First real run (run-20261016-203939, fastapi-agentcore, stopped by hand at ~$1.10 reported): it
    reached `dev_pair`. Findings: the sandbox has Python 3.12 only under `/opt/amazon/genesis1p-tools/venv/bin`,
    no pytest, no git, no internet, aarch64, uid 991; `project.test` exited 127 (our PATH dropped Python).
    Reported cost ($1.10) was 2-3x the list price of the same tokens ($0.39-$0.52).
  - Added after it: `sandbox.requirements` (wheels installed into the run workspace from the runtime,
    `PYTHONPATH` + Amazon Python in PATH for the sandbox), `price` per agent (cost computed from it,
    provider figure kept as `provider_cost_usd`), `heartbeat` events, CloudWatch dashboard `magi-runs`
    (model vs infrastructure cost, run filter), S3 lifecycle IAM actions fixed
    (`s3:Get/PutLifecycleConfiguration`), `DescribeMountTargets` on `*`.
  - `fetch-result.sh` and `try-candidate.sh` (deploy/agentcore) download a run's patch and try it:
    tests, Docker build, container, smoke requests. Tried end to end on the target with a sample patch.
  - Second run (run-20261016-211807, stopped at 0.35 computed / 0.95 provider-reported USD): the toolchain
    install worked but `git clean -fdx` in `WorkspaceManager.restore` deleted `.magi-toolchain`
    (`project.test` exit 1, no pytest). Fixed with `git clean -e .magi-toolchain`; image `tc-2`.
  - Third run (run-20261016-213811): `project.test` and 10/12 acceptance scenarios PASS in the sandbox;
    ended INFRA_FAILURE because the repo's lint gate needs `flake8`, absent from the sandbox toolchain.
    Cost: 0.549 USD at list price vs 1.458 USD reported by OpenRouter (2.7x, `sort: throughput` routing).
    The provider figure is the one to trust: `price` stays optional and unset.
  - Fourth run (run-20261016-221108, `sandbox.packages: [flake8]`, 0.61 USD, 71 calls): ended
    BUDGET_EXHAUSTED on duration (3862 s > 3600 s) with no patch. Agents ran heredoc commands
    (`python - <<'PYEOF'`) that waited on stdin until the command timeout (~616 s each). Fixed in
    `CodeInterpreterRunner.remote_command` (`< /dev/null`); image rebuild and a new run still to do.
  - Code is no longer in the image (a rebuild per change was unacceptable: 6 rebuilds in one session, none for
    a dependency change). The image is a base (Python, git, uv, locked deps, `bootstrap.py`); `src/magi_system`
    is a zip in S3 published by `deploy/agentcore/push_code.py` and loaded at session start. Rebuild the image
    only when `pyproject.toml` / `uv.lock` change. Code written and tested, **not yet deployed**.
  - Runs 092321 (INFRA_FAILURE: QA scenario that text-searches source), 101618 and 103712/111040
    (UNRESOLVED_REQUIREMENT: true claims in the wrong format "refuted"). Found late: AgentCore hands new sessions
    warm containers that loaded the OLD code bundle; 111040 ran code 4f1e77d7dbfa although 2d98576e6b86 was published, so
    its fix was never exercised. `run_and_log.py` now checks the code version of the run, stops it before any model
    call if it is stale and retries (3 attempts needed in the test). Fixes made: scenarios that text-search source or
    select no tests are advisory (`engine/gates.py`), malformed symbol/policy claims are rejected in `ClaimDraft` so the
    agent retries (`engine/schemas.py`). Not yet proven in a full run.
  - QA can now correct a scenario that FAILED after seeing the code (once per scenario, the corrected command is
    executed; `scenario_amended` event): `docs/state-and-failures.md`. Tested at unit level only, not in a full run.
  - Still to confirm on AWS: a run whose `project.test` passes in the sandbox with the installed wheels,
    and the computed cost against the OpenRouter Activity page.
- CI: `uv sync --extra agentcore`; new `terraform` job (fmt/init/validate, no credentials).
- Tests: **259 passed**. Lint is clean. `magi demo` reaches SUCCESS.

Next (needs explicit approval, fake model only): the AWS spike in
`docs/agentcore/testing.md#spike-checklist-before-the-first-real-run`. Key unknowns:
- Code Interpreter `executeCommand` response and toolchain (no custom images; the fallback is
  CodeBuild, designed but not built)
- git/flock on EFS
- IAM tag conditions
- a 60-minute HealthyBusy session
- cost

Not implemented:
- automatic resume
- Git credentials for private targets
- the CodeBuild runner

## What exists

| Area | Where |
|---|---|
| CLI `magi run|resume|status|report|demo` | `src/magi_system/cli.py` |
| Application service, outcome, reports, resume checks | `src/magi_system/service/app.py`, `store.py` (SQLite / DynamoDB) |
| AgentCore entrypoint, image, Terraform | `src/magi_system/interfaces/agentcore.py`, `deploy/agentcore/`, `docs/agentcore/` |
| Graph A–G + triage, subgraphs, reducers | `src/magi_system/engine/` |
| MAGI decision (verifier + selection) | `src/magi_system/decision/` |
| Agents, prompts (versioned), LLM adapters | `agents/`, `prompts/`, `llm/` |
| Workspace / Git, runners (container, local, Code Interpreter), tools, policy, project config | `workspace/`, `execution/`, `tools/`, `policy/`, `config/` |
| Offline scenario + demo target | `src/magi_system/offline.py` |
| GitHub Action / CI | `action.yml`, `.github/workflows/ci.yml`, `docs/github-actions.md` |
| Docs | `README.md`, `docs/architecture.md`, `docs/state-and-failures.md`, `docs/security.md`, `docs/providers.md`, `docs/limits.md` |
| Examples | `examples/request.md`, `examples/settings.example.yaml`, `examples/magi-config.example.yaml` |

## Test coverage map

| Brief | Tests |
|---|---|
| §14 #1 three valid, deterministic selection | `test_magi_decision::test_three_valid_proposals_deterministic_selection` |
| #2 one admissible | `test_only_one_admissible_proposal` |
| #3 none admissible | `test_no_admissible_proposal_terminates_after_bounded_revision` |
| #4 two favourable + confirmed blocker | `test_confirmed_blocker_beats_two_favourable_votes` |
| #5 invented objection refuted | `test_invented_objection_is_refuted_and_not_a_veto`, `test_objection_without_evidence_is_unresolved_not_a_veto` |
| #6 nonexistent symbol / API | `test_nonexistent_symbol_makes_proposal_inadmissible`, `test_capability_missing_in_version_and_nonexistent_policy` |
| #7 unresolved critical unknown | `test_unresolved_critical_unknown_blocks` |
| #8 common-mode error | `test_common_mode_error_caught_by_external_check` |
| #9 timeout + invalid output | `test_magi_timeout_and_invalid_output_are_typed_failures`, `test_persistent_magi_failure_stops_without_partial_selection`, `test_quorum_profile_allows_partial_results_explicitly` |
| #10 budget exhaustion | `test_budget_exhaustion`, `tests/unit/test_support.py` |
| #11 DEV1 then DEV2 | `test_e2e::test_dev1_then_dev2_never_in_parallel` |
| #12 pair no changes | `test_e2e::test_pair_without_changes` |
| #13 pair bug caught | `test_fix_loops::test_pair_introduced_bug_caught_by_gates_and_fixed_by_pair` |
| #14 fix invalidates evidence | `test_fix_invalidates_previous_evidence`, `test_writer_bug_goes_back_to_writer_then_pair_again` |
| #15 resume without duplicates | `test_resume_and_state::test_resume_after_crash_does_not_duplicate_effects`, `test_writer_step_crash_after_commit_reuses_commit` |
| #16 concurrency / reducers | `test_reducers_concurrent_updates_lose_nothing`, `test_fan_in_waits_for_all_three_magi_results` |
| #17 isolation rounds / runs | `test_rounds_do_not_mix`, `test_two_concurrent_runs_on_two_projects_are_isolated` |
| #18 protected policy / graders | `test_fix_loops::test_agents_cannot_delete_tests_weaken_gates_or_touch_protected_files`, `test_commands_run_by_agents_cannot_modify_protected_files` |
| #19 no human wait | `test_no_human_wait_paths` |
| #20 no SUCCESS with missing checks | `test_no_success_when_a_mandatory_check_cannot_run`, `test_not_applicable_requires_policy_and_justification` |
| e2e | `test_e2e::test_end_to_end_success` |
| §0.7 #1–3, 5–9, 11–13, 14 | `tests/integration/test_multi_repo.py` |
| §0.7 #4, #10 | `test_resume_and_state.py` |
| architecture escalation route | `test_architecture_escalation_starts_a_new_round_and_plan` |

## Not done / next candidates

See `docs/limits.md`. In priority order:
1. Push and watch the first CI run (`test` + `action-selftest`) — user's decision.
2. First real-LLM run on a throwaway target (OpenRouter, `OPENROUTER_API_KEY`) — in progress.
3. Tag a release (`v1`) so consumers can pin `uses: lorenzogirardi/magi-system@v1`.
4. `magi cleanup` / retention command (WorkspaceManager.prune exists).
5. Proposal merging (new versioned proposal from parts) — not implemented.
6. Starting `services` declared in `.magi/config.yaml` — parsed, not started.

## Design decisions

- Graph state JSON-native, merged by id; deps in Runtime context; checkpoint serde strict.
- Node wrapper must not use `functools.wraps` (LangGraph would see the inner signature and
  not inject `runtime`).
- Idempotent agent steps via Git refs `refs/tags/magi-step/<step>`.
- All verification results keyed by candidate commit; gate reads only the final commit.
- Fan-in quorum 3 by default; `quorum-2` / `offline-test-quorum-2` profiles opt in to 2.
- Derived requirements never mandatory; explicit ones need a verbatim request quote.
- Findings CONFIRMED only when all claims are tool-SUPPORTED; no claims → UNRESOLVED.
- `.magi/config.yaml` authorization keys → POLICY_DENIED; partial config completed by discovery.
- Node updates are redacted before checkpointing; credentialed remote URLs rejected.
- Default profile requires ContainerRunner; LocalRunner only in explicit profiles.
- Container runs refuse to start when the image is absent (never pull; avoids misreading
  "image not known" as a failing check).
- GitHub integration = composite action only; it uploads the patch, never pushes.

## Session notes

- Use default permission mode (auto mode blocked a command in session 1).
- Throwaway prototypes in `/tmp/lgproto/` (outside repo).
- Stray `.README.md.swp` in repo root left untouched (now gitignored).

### Session 6 follow-up — why run 144624 ended FAILED_VERIFICATION (2026-10-17)
- Run 144624 (1.44 USD model cost, 156 calls): every executed gate PASS; `project.build` and `project.lint` were UNKNOWN
  because the target `fastapi-agentcore` has no `.magi/config.yaml`, so no build/lint command exists. Found only at the end.
- Fix: **preflight at intake** (`gates.project_gate_problems`, `nodes_intake.intake`): a mandatory project gate that cannot run
  stops the run as `UNRESOLVED_REQUIREMENT` before any model call. Tests: `test_no_success_when_a_mandatory_check_cannot_run`,
  `test_unauthorized_project_command_is_not_executed`.
- Next: the target needs `.magi/config.yaml` (build `python -m compileall -q app`, test `python -m pytest -q`, lint
  `python -m flake8 . --count --select=E9,F63,F7,F82 ...`; validated locally with `resolve_project_config`). Not pushed: operator decides.
- Cost analysis of the run: tool-loop turns dominate (dev.writer 25, dev.pair 24, tech_writer 24 calls); not the blocker, left as is.

### First SUCCESS on AgentCore (2026-10-17)
- `run-20261017-155113`: SUCCESS, 116 calls, 1.35 USD model + ~0.12 infra, 25 min 44 s, all 10 mandatory gates PASS, 21-file patch.
  Target `.magi/config.yaml` pushed to `fastapi-agentcore` main (d615121); code bundle `1ff47e162530` (preflight).
- Independent check (`try-candidate.sh` with Docker): patch applies, 133 tests pass, image builds, `/api/mgmt/ready` 200,
  the 3 toolbox tools answer 200. Case study: `examples/run-20261017-155113-agentcore/{article,training}.md`.
- Warm containers served old code on 4 of 5 starts; the stale-code guard handled it at zero cost. A runtime version bump
  (`terraform apply`) would force fresh containers; not needed this time.

