# STATUS — magi-system

Last updated: 2026-10-15 (session 4, OpenRouter provider + first local real-target prep).

## State

v1 is implemented and runs end-to-end offline. `uv run magi demo` → SUCCESS on a
temporary greeter repo (POSIX sh/Makefile stack, distinct from magi-system).
`uv run pytest` → **189 passed, 1 skipped** (~65 s; the real-container test runs when
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

## What exists

| Area | Where |
|---|---|
| CLI `magi run|resume|status|report|demo` | `src/magi_system/cli.py` |
| Application service, outcome, reports, resume checks | `src/magi_system/service/app.py`, `store.py` |
| Graph A–G + triage, subgraphs, reducers | `src/magi_system/engine/` |
| MAGI decision (verifier + selection) | `src/magi_system/decision/` |
| Agents, prompts (versioned), LLM adapters | `agents/`, `prompts/`, `llm/` |
| Workspace / Git, runners, tools, policy, project config | `workspace/`, `execution/`, `tools/`, `policy/`, `config/` |
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
