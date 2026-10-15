# MAGI run report — run-20261015-205501-e0a70f

- Target: `pytbak-target` (<target-repo>)
- Baseline commit: `e3920b57784c30b69207c5e2d9252b48c5dc08a5`
- Status: **SUCCESS**
- Final candidate: `5cce89015d76b5ae76f53df705699179da21dfe3` (plan version 1)
- Verified change: yes · Integrated (merged): no · Deployed: no
- Diagnosis: all mandatory gates passed on the final candidate (verified change only; not integrated or deployed)

## Mandatory gates

| Gate | Verdict | Commit | Detail |
|---|---|---|---|
| project.build | PASS | `5cce89015d76` | exit 0 |
| project.test | PASS | `5cce89015d76` | exit 0 |
| project.lint | PASS | `5cce89015d76` | exit 0 |
| qa.acceptance | PASS | `5cce89015d76` | 11 acceptance scenario(s) passed; QA review without confirmed blockers |
| security.review | PASS | `5cce89015d76` | review completed; 8 finding(s), none confirmed blocking |
| sre.review | PASS | `5cce89015d76` | review completed; 5 finding(s), none confirmed blocking |
| docs.updated | PASS | `5cce89015d76` | documentation updated: ['README.md', 'docs/14-dev-sre-toolbox.md'] |
| magi.final_review | PASS | `5cce89015d76` | three final reviews completed; 7 finding(s), none confirmed blocking |
| integrity.protected_paths | PASS | `5cce89015d76` | no protected change |
| scope.write_scopes | PASS | `5cce89015d76` | all changes inside authorized scopes |

## Publication actually performed

- export_patch: artifacts/candidate.patch
- local_branch: run-20261015-205501-e0a70f/workspace#magi/run-20261015-205501-e0a70f

No push, pull request, merge or deployment was performed.

## Findings

- [REFUTED] minor tooling — No new regression test module; regression relies on running existing tests unchanged (spec.qa)
- [CONFIRMED] minor operations — No per-tool metrics; only route-level instrumentation is available (spec.sre)
- [UNRESOLVED] minor documentation — CHANGELOG.md is omitted from changes and documentation impact (doc.tech_writer)
- [REFUTED] minor operations — Unknown tool returns 404 with a body rewritten by the app-wide handler (magi.balthasar)
- [REFUTED] info tooling — Final diff is truncated; core modules not reviewable in this context (magi.balthasar)
- [CONFIRMED] minor operations — Unknown tool returns 404 whose body is masked by the app-wide 404 handler (spec.sre)
- [CONFIRMED] minor tooling — Provided diff is truncated: router, models, base, registry, settings and main are not visible (spec.qa)
- [CONFIRMED] major operations — No per-run timeout or concurrency bound on tool execution (spec.sre)
- [CONFIRMED] minor architecture — Critical unresolved assumption on the YAML dependency and image build (magi.casper)
- [CONFIRMED] minor tooling — Unresolved dependency-location assumption (magi.melchior)
- [CONFIRMED] minor documentation — README route count updated by assumption, not by measurement (doc.tech_writer)
- [CONFIRMED] info operations — No new infrastructure; run_id is correlation-only and not persisted (spec.sre)
- [CONFIRMED] major operations — New hard dependency is imported at application-import time; a missing install takes the whole app down (spec.sre)
- [CONFIRMED] minor documentation — README.md not updated: stale route count and no toolbox section (dev.pair)
- [REFUTED] minor design — allowPrivilegeEscalation absent is mapped to severity warning (spec.qa)
- [CONFIRMED] major operations — run_id is not propagated to tracing, so response/log/trace correlation is not achieved (spec.sre)
- [CONFIRMED] major operations — Unknown-tool contract is left undecided in the plan (spec.sre)
- [REFUTED] major design — Unknown-tool response contract is undecided (magi.melchior)
- [CONFIRMED] info design — Findings-cap behaviour is a controlled analysis outcome, not an HTTP error (magi.balthasar)
- [CONFIRMED] minor tooling — No new regression test module for existing endpoints (magi.casper)
- [CONFIRMED] info operations — No new infrastructure, persistence or retrieval endpoint introduced (spec.sre)
- [CONFIRMED] minor design — Unknown tool id returns 404 whose body is masked by the app-wide 404 handler (spec.security)
- [REFUTED] info design — Findings cap handled as analysis outcome rather than a rejection (magi.casper)
- [CONFIRMED] minor operations — Inconsistent limit signalling inside the same tool (spec.sre)
- [CONFIRMED] minor security — CPU-bound tool execution shares the process-wide default thread pool with other endpoints (spec.security)
- [CONFIRMED] minor security — Router-level security and observability claims are not verifiable from the diff (spec.qa)
- [UNRESOLVED] minor operations — Request-size cap likely depends on Content-Length and may be bypassable (spec.sre)
- [CONFIRMED] minor operations — Observability guarantees are documented but not asserted by any executed check (spec.sre)
- [CONFIRMED] major operations — PyYAML availability in the runtime image is an unresolved critical assumption (spec.security)
- [CONFIRMED] minor tooling — Critical assumption about docker/requirements.txt is left unresolved (spec.qa)
- [CONFIRMED] info tooling — PyYAML pin differs from the approved plan (spec.sre)
- [REFUTED] minor documentation — Pinned PyYAML version differs between plan and diff (magi.balthasar)
- [CONFIRMED] minor documentation — Unknown-tool 404 body must be documented as the generic envelope (doc.tech_writer)
- [REFUTED] minor design — TOOLBOX_ENABLED kill switch is not mandated and adds configuration surface (spec.qa)
- [REFUTED] major design — Unknown-tool behaviour is left undecided in the contract (magi.casper)
- [CONFIRMED] minor operations — New toolbox limits are not surfaced in .env.example, weakening deploy-time discoverability (spec.sre)
- [CONFIRMED] major documentation — curl examples must carry the Basic Auth header to be 'working' (doc.tech_writer)
- [CONFIRMED] info tooling — Pinned PyYAML version differs from the plan (spec.security)
- [CONFIRMED] minor operations — Findings-cap edge case: max_findings=0 still returns one finding (spec.sre)
- [CONFIRMED] minor tooling — OpenAPI schema presence is only in the verification plan, not in a test module (spec.qa)
- [CONFIRMED] minor operations — Observability log line is claimed but not asserted by any test (spec.sre)
- [REFUTED] minor design — Inconsistent limit-handling surface (magi.casper)
- [CONFIRMED] info design — json.validate treats URL/path-like strings as text and returns a negative analysis result (spec.qa)
- [REFUTED] major design — YAML document/depth limits return 200 with a finding instead of a controlled error (spec.qa)
- [CONFIRMED] minor security — json.loads recursion depth is not visibly guarded (spec.security)
- [CONFIRMED] minor operations — run_id is not propagated to tracing, so response/log/trace correlation is not achieved (spec.sre)
- [REFUTED] info documentation — Finding code typo in the json.validate contract (magi.balthasar)
- [UNRESOLVED] info security — Toolbox endpoints reuse the existing Basic Auth dependency (spec.security)
- [REFUTED] major design — Unknown-tool contract is left ambiguous, so the acceptance test cannot be written deterministically (spec.qa)
- [UNRESOLVED] info operations — Request-size limit relies on Content-Length; per-field input limit remains the backstop (spec.sre)
- [UNRESOLVED] info security — json.validate echoes the parsed value back in the response body (spec.security)
- [CONFIRMED] minor documentation — Single document mixes all four Diátaxis modes (doc.tech_writer)
- [UNRESOLVED] major design — Unknown-tool contract is left unresolved in the proposal (spec.security)
- [CONFIRMED] minor design — Configurable limit settings fields are not verifiable from the diff (spec.qa)
- [CONFIRMED] major documentation — New environment settings are not documented in .env.example (doc.tech_writer)
- [UNRESOLVED] minor design — Unknown tool id mapped to HTTP 422 conflates validation and lookup errors (spec.security)
- [REFUTED] minor design — allowPrivilegeEscalation absent is reported as error, not as missing configuration (magi.balthasar)
- [REFUTED] minor design — allowPrivilegeEscalation absent is mapped to severity error, conflating missing config with explicit risk (spec.qa)
- [CONFIRMED] minor security — CPU-bound tool execution shares the process-wide default thread pool with other endpoints (spec.security)
- [CONFIRMED] minor security — CPU-bound tool execution shares the process-wide default thread pool with other endpoints (spec.security)
- [REFUTED] info design — Non-mandated TOOLBOX_ENABLED kill switch adds configuration surface (magi.casper)
- [CONFIRMED] major documentation — curl examples must carry the Basic Auth header to be 'working' (doc.tech_writer)
- [CONFIRMED] major operations — New hard dependency is imported at application-import time; a missing install takes the whole app down (spec.sre)
- [CONFIRMED] info design — Findings cap truncates the list but appends an explicit notice finding (spec.qa)
- [CONFIRMED] minor operations — Two distinct limit channels must both be documented and monitored (spec.sre)
- [CONFIRMED] blocker local_bug — New test test_empty_documents_are_skipped has a wrong expectation and fails (dev.writer)
- [CONFIRMED] minor design — Unknown tool returns 404 with the generic app-wide body, hiding the reason (spec.qa)
- [CONFIRMED] major operations — New hard dependency is imported at application-import time; a missing install takes the whole app down (spec.sre)
- [CONFIRMED] minor documentation — New toolbox env variables are not added to .env.example (spec.security)
- [REFUTED] minor local_bug — Image reference with an empty tag is not flagged (magi.balthasar)
- [UNRESOLVED] info security — json.validate echoes the parsed value back in the response body (spec.security)
- [CONFIRMED] minor local_bug — Findings-limit edge case when max_findings is 0 (spec.security)
- [CONFIRMED] info operations — Findings cap behaviour matches the no-silent-truncation requirement (spec.sre)
- [UNRESOLVED] info security — Internal errors are sanitized (spec.security)
- [CONFIRMED] major documentation — README.md was not updated (edit command failed) (dev.writer)
- [CONFIRMED] major operations — run_id is not propagated to tracing, so response/log/trace correlation is not achieved (spec.sre)
- [REFUTED] minor tooling — PyYAML pin not reconciled with the installed version (dev.writer)
- [CONFIRMED] minor security — json.validate has no recursion/depth guard for deeply nested JSON (magi.balthasar)
- [UNRESOLVED] info security — json.validate echoes the parsed value back in the response body (spec.security)
- [CONFIRMED] info tooling — Type checking not available in this repository (dev.writer)
- [REFUTED] minor documentation — Critical claim about protected paths is marked refuted by verification (magi.casper)
- [CONFIRMED] major operations — No per-run timeout or concurrency bound on tool execution (spec.sre)
- [REFUTED] major documentation — Critical claim about protected paths is refuted (magi.melchior)
- [REFUTED] minor design — YAML depth limit enforced after parsing (magi.melchior)
- [CONFIRMED] minor operations — No per-tool metrics; only route-level instrumentation is available (spec.sre)
- [CONFIRMED] major documentation — curl examples must carry the Basic Auth header to be 'working' (doc.tech_writer)
- [CONFIRMED] info tooling — Ad-hoc app.routes enumeration does not show the included routers (dev.pair)
- [CONFIRMED] minor operations — TOOLBOX_ENABLED kill switch adds an untested configuration path and an ambiguous disabled behaviour (spec.sre)
- [CONFIRMED] minor documentation — Document number 14 is assumed free (doc.tech_writer)
- [CONFIRMED] minor operations — No per-tool metrics; only route-level instrumentation is available (spec.sre)
- [REFUTED] info security — YAML parsing is safe and rejects aliases (spec.security)
- [CONFIRMED] minor tooling — No-network guarantee is asserted only by static import inspection (spec.qa)
- [CONFIRMED] major operations — No per-run timeout or concurrency bound on tool execution (spec.sre)
- [CONFIRMED] minor documentation — Single document mixes all four Diátaxis modes (doc.tech_writer)
- [REFUTED] minor design — Application logic partly lives in the router (magi.balthasar)
- [REFUTED] info operations — Verification evidence is consistent with the acceptance criteria (magi.balthasar)
- [CONFIRMED] minor documentation — Single document mixes all four Diátaxis modes (doc.tech_writer)
- [CONFIRMED] minor security — Review limited by truncated diff (spec.security)
- [CONFIRMED] major documentation — Unknown-tool contract is undecided, so the API reference cannot be written (doc.tech_writer)
- [CONFIRMED] info documentation — New TOOLBOX_* settings are not added to .env.example (magi.balthasar)
- [REFUTED] info design — YAML depth-limit enforcement not specified (magi.balthasar)
