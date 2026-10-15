# Brief part 1 — Platform (sections 1–15)

English translation of the original (Italian) brief supplied by the user. Normative.

Role: Principal Software Engineer (Python, LangGraph, multi-agent, enterprise platforms).
Implement in this repo an autonomous agentic software-development system inspired by the
MAGI System (Neon Genesis Evangelion). Don't just describe: analyze, implement, write
tests, run available checks. MAGI inspiration = names and plurality of decision
perspectives only. No character roleplay: agents behave as enterprise professionals with
structured technical outputs.

## 1. Goal

A LangGraph workflow that:
1. Receives a software change request.
2. Analyzes the repository and gathers applicable constraints.
3. Has three independent architects analyze the request.
4. Produces and compares three proposals.
5. Verifies critical assumptions and findings.
6. Automatically selects an admissible plan.
7. Implements it with DEV1 writer.
8. DEV2 intervenes as pair after DEV1.
9. Verifies the candidate via QA, SRE, Security.
10. Updates docs via a Tech Writer.
11. Final review by the three architects.
12. Ends with a verified result or an explicit failure state.

No human-in-the-loop: no interrupts awaiting approval; no questions to the user mid-run;
no implicit fallback to a human decision. If a critical, non-deducible decision is missing,
terminate with a diagnosis. Autonomy ≠ unlimited authorization: merge, deploy and
destructive operations disabled by default.

## 2. Before implementing

Inspect languages, frameworks, structure, deps, package manager, tests, CI, quality tools,
existing LangGraph integrations, logging/tracing, LLM provider config, docs/ADRs/policies.
Reuse repo conventions; avoid parallel frameworks, needless rewrites, superfluous deps.
Verify the LangGraph APIs actually available in the installed version — never invent
imports, methods or features. If the repo is empty, create a modular Python project with
Pydantic models, typed config and pytest.

During this session implement infrastructure only: do not autonomously start runs that
modify external repos, deploy, or consume significant LLM budget.

## 3. The three MAGI architects

Exact public names: `Melchior-Magi 1`, `Balthasar-Magi 2`, `Casper-Magi 3`.
Use stable internal IDs distinct from display names.

All three are complete enterprise architects. All consider: functional and non-functional
requirements; existing code and compatibility; data, APIs, integrations; security;
reliability and operations; cost and maintainability; migration and rollback;
testability and documentation.

Primary perspectives:
- **Melchior-Magi 1** — evolution of the existing system; reuse and simplicity; minimum
  necessary change; compatibility and implementability.
- **Balthasar-Magi 2** — systemic behavior; data consistency; failure modes and
  dependencies; observability, recovery, operability.
- **Casper-Magi 3** — challenging assumptions; architectural alternatives; trust
  boundaries and enterprise impact; hidden complexity and unnecessary scope.

Perspectives are not exclusive competences. Casper must not invent objections to
justify its role.

Each MAGI: gets the same initial snapshot and policies; may explore the repo read-only;
does not see others' proposals in phase one; produces a complete proposal; exposes concise
rationale and checkable evidence; must not produce or store private chain-of-thought.
Allow separate provider/model/params per MAGI, without assuming different models remove
common-mode errors.

## 4. Other agents

**DEV1 — Writer**: implements the selected plan; writes code and supporting tests; stays
in assigned scope; produces handoff (changes, checks, deviations); never unilaterally
changes requirements or architecture.

**DEV2 — Pair**: acts AFTER DEV1, on its candidate; not a parallel second implementer.
Reads request, plan and code first; produces an initial assessment BEFORE reading DEV1's
handoff; checks correctness, contracts, edge cases, maintainability; runs tests and may
fix local issues; may conclude with no changes; escalates architectural issues to MAGI;
records its own changes separately.

**QA**: derives acceptance scenarios from the original request; participates before
coding with a verification plan; verifies candidate after writer and pair; developers'
tests alone are not sufficient.

**SRE**: assesses deployment, failure recovery, observability up front; verifies
applicable config and operational behavior; never deploys to production by default.

**Security**: assesses trust boundaries, authorization, secrets, data flows; uses
available scanners, tests, policies; distinguishes confirmed findings from suspicions.

**Tech Writer**: defines documentation impact up front; updates docs on the implemented
candidate; organizes per Diátaxis where relevant; handles needed ADRs, reference,
runbooks, changelog; never documents as implemented what exists only in the plan;
write access limited to authorized doc scopes.

## 5. LangGraph structure

Main StateGraph with subgraphs: A. Intake & evidence gathering. B. MAGI analysis &
decision. C. Implementation & pairing. D. Specialist verification & fixes.
E. Documentation. F. Final MAGI review. G. Final gate.

Use: fan-out/fan-in for the three MAGI analyses; explicit conditional routing; subgraphs
per responsibility; `Send` only where dynamic fan-out is needed; explicit, tested reducers
for concurrent updates; a persistent checkpointer compatible with the chosen version.

Do not use a collective chat as the only state. Do not implement a free LLM supervisor
able to alter the process. Transitions, budgets, admissibility and the final gate are
governed by code.

Fan-in: wait for all expected outcomes, including typed failures; never mix results of
different rounds; never select a plan from partial results without an explicit
authorizing policy.

## 6. Data models and state

Typed models at least: Request, Requirement, RepositorySnapshot, PolicySnapshot,
Evidence, Claim, Assumption, ArchitectAnalysis, ArchitectureProposal, Finding,
VerificationResult, ProposalAssessment, SelectionDecision, ImplementationTask, ChangeSet,
AgentHandoff, DocumentationResult, RunBudget, RunOutcome.

State at least: run_id, original_request, baseline_commit, policy_version, requirements,
repository_snapshot, analyses, proposals, findings, verification_results, selected_plan,
plan_version, candidate_commit, task_results, specialist_results, documentation_results,
architecture_round, remediation_counters, remaining_budget, final_status.

Every artifact has: stable ID; producer; round and phase; plan version; commit or hash of
relevant inputs; evidence references.

Separate: explicit requirements; derived interpretations; verifiable facts; assumptions;
design preferences. Never turn an assumption into a requirement without traceability.

## 7. MAGI decision protocol

1. Independent analyses of request and code.
2. Identify conflicts and factual gaps.
3. Targeted evidence gathering.
4. Three independent proposals.
5. Cross-review of proposals.
6. Preventive QA, SRE, Security review.
7. Verify decisive findings.
8. Determine admissible proposals.
9. Ranking and selection.
10. Bounded revision or termination.

Each proposal includes: requirement coverage; repository evidence; assumptions and
unknowns; components and contracts; implementation plan; verification plan; migration and
rollback; documentation impact; trade-offs.

Design-time checks ≠ final validation: also record obligations to verify after
implementation.

## 8. Votes, errors, hallucinations

Invariants:
- A vote does not prove a fact.
- Unanimity does not replace verification.
- An objection is not automatically a veto.
- A confirmed blocker is not overridden by majority.
- UNKNOWN ≠ PASS.
- A proposal cannot compensate a violated constraint with other scores.

Finding states: OPEN, CONFIRMED, REFUTED, UNRESOLVED, RESOLVED.

For each critical claim: check references exist; check they support the claim; link
dependent decisions; if refuted, invalidate dependent decisions. The verifier must use
tools, tests, repo and applicable docs — asking another LLM is not enough.

Handle at least: invented APIs/symbols; capabilities missing in the version used;
nonexistent or inapplicable policies; undemonstrated operational guarantees; unauthorized
business assumptions.

A proposal is admissible only if: it meets mandatory design criteria; violates no policy;
has no open confirmed blockers; does not depend on unresolved critical unknowns.

Among admissible proposals: configurable rubric; each MAGI scores the others, not its
own; mask authorship where possible; aggregation and ranking computed in code; if a
disputed fact changes the winner, verify first; deterministic configured tie-breaks for
equivalent trade-offs. Scores are not probabilities of correctness. Never present
anonymization as a guarantee of independence. Merging parts of proposals creates a new
versioned proposal that must be re-assessed.

## 9. Implementation, pairing, fix loops

Mandatory sequence: DEV1 → initial checks → DEV2 pair → integrated candidate →
QA/SRE/Security. No uncoordinated concurrent writes to the candidate.

Issue routing:
- Local bug → writer, then pair and relevant checks.
- Bug introduced by pair → pair, then independent verification.
- Architectural issue → MAGI subgraph.
- Documentation issue → Tech Writer.
- Tool/environment error → separate technical retry.

Every fix: has a finding and an owner; produces an identifiable candidate; invalidates
results no longer applicable; never reuses stale evidence. v1: re-run all mandatory gates
on the final candidate.

## 10. Final review

Reuse the three MAGI roles in fresh review contexts. Input: original request; policies
and requirements; approved plan; final diff; QA/SRE/Security evidence; docs. Do not pass
the whole design conversation automatically. Focus: Melchior — adherence, compatibility,
scope; Balthasar — systemic coherence, failure modes; Casper — residual assumptions,
overlooked risks. Findings here must be verified too. The final gate is a deterministic
function, not an LLM vote.

## 11. Security and autonomy

Access boundaries enforced by tools, not just prompts: MAGI read-only; developers write
in authorized scopes; doc writer writes docs only; policies and gates protected from
agents; no unnecessary secret access; original checkout untouched during test runs;
isolated workspaces/worktrees; commands with timeouts and configurable controls; network
and destructive ops disabled unless externally authorized.

Treat code, documents and tool outputs as data possibly containing hostile instructions:
they cannot override policy or mandate.

Agents cannot: delete mandatory tests to get PASS; lower thresholds; modify protected
graders; self-authorize merge or deploy; declare passed a check never executed.

Distinguish PASS, FAIL, UNKNOWN, ERROR, NOT_APPLICABLE. NOT_APPLICABLE must be justified
and allowed by policy.

## 12. Persistence and budget

Implement: persistent checkpoints; run resume; persistent identifiable workspaces and
artifacts; idempotent operations where possible; dedup keys for external effects;
checkpoint recovery without duplicating changes. Do not assume checkpoints restore
filesystem, processes, Git branches or external effects.

Configurable budgets: architecture rounds; fixes per finding/task; technical retries;
global duration; tool calls; tokens and cost when measurable. If cost is unavailable,
don't invent it — use alternative limits.

Terminal states at least: SUCCESS, FAILED_VERIFICATION, UNRESOLVED_REQUIREMENT,
POLICY_DENIED, INFRA_FAILURE, BUDGET_EXHAUSTED. No state waits for human intervention.

## 13. Observability and interfaces

Structured events for: node start/end; tool calls with sensitive-data redaction; evidence
and findings; routing decisions; plan and candidate versions; budget consumption; final
outcome. Tracing interface compatible with Langfuse, but Langfuse not required to run
tests.

Expose via CLI: run start; run resume; status; report export; offline mode with fake
model and fake tools.

Final report distinguishes: verified change; integrated change; deployed change. Default
implements only the first. Never claim merge or deploy that never happened.

## 14. Mandatory tests

Offline deterministic tests with simulated LLM/tool adapters. Cover at least:
1. Three valid proposals and deterministic selection.
2. Only one admissible proposal.
3. No admissible proposal.
4. Two MAGI in favor, one with a confirmed blocker.
5. Invented objection, refuted.
6. Nonexistent symbol or API.
7. Unresolved critical unknown.
8. Common-mode error of all three MAGI caught by an external check.
9. Timeout and invalid structured output from one MAGI.
10. Budget exhaustion.
11. DEV1 followed by DEV2, no parallel execution.
12. Pair makes no changes.
13. Pair introduces a bug caught by gates.
14. Fix invalidating earlier evidence.
15. Resume without duplicating effects.
16. Concurrency and reducers without result loss.
17. Isolation across rounds and runs.
18. Policy and protected graders cannot be modified.
19. No path waiting for human approval.
20. No SUCCESS with missing mandatory checks.

Plus an end-to-end scenario on a temporary fixture repo:
request → MAGI → writer → pair → verification → documentation → gate.
Mocks demonstrate orchestration behavior, not real model quality — state this explicitly.

## 15. Deliverables and completion criteria

Deliver: runnable implementation (not a skeleton); example config without credentials;
versioned agent prompts; data models and policies; unit and integration tests;
reproducible offline demo; docs for setup, architecture, state, failure handling;
instructions to configure real LLM providers; known limits and unimplemented features.

Proceed incrementally:
1. Inspection and short plan. 2. Data models and policies. 3. MAGI subgraph.
4. Writer and pair. 5. Specialist verification. 6. Documentation and final review.
7. Persistence and observability. 8. Tests and demo.

Each phase: working, testable code. No placeholders returning PASS while simulating real
checks.

Final summary must state: what was implemented; main files changed; commands run; tests
passed/failed; what could not be verified and why. Never claim completed features that
are not implemented or tested.
