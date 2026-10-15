# Brief part 0 — Multi-repository architecture

English translation of the original (Italian) brief supplied by the user. Normative.

## 0. Control repository vs target repository

`magi-system` holds the agentic platform, NOT the code of the application projects the
platform works on. Implement it as an autonomous, reusable engine usable by other Git repos.

Always distinguish:

1. **Control repository** — `magi-system`: LangGraph orchestration, agent implementations,
   versioned prompts, platform policies and gates, LLM and tool adapters, workspace
   management, persistence/logging/tracing, CLI and integration contracts.
2. **Target repository** — the software project a run works on: application code, tests,
   build/deploy config, docs, optional MAGI integration config.
3. **Run workspace** — the isolated working copy of one target repository, bound to one run
   and one precise starting revision.

Never conflate these three. At run time agents operate exclusively on the authorized target
workspace. They must never modify control-repo code, prompts, policies or gates to make a
target check pass.

## 0.1 Initial usage mode

Initially implement a CLI running in the caller's environment. Two modes:

- **A. Local target** — caller passes a path to a local Git repository.
- **B. Remote target** — caller passes a remote Git URL.

In both cases:
- Resolve the requested revision to a precise commit.
- Prepare an isolated workspace.
- Never modify the caller's original checkout.
- Never silently include uncommitted local changes. Including them requires an explicit
  option, and the patch used is recorded as a run input.

CLI contract (commands to implement):

```
magi run --repo /path/to/project --ref <commit-or-ref> --request-file request.md
magi run --repo <git-remote-url> --ref <commit-or-ref> --request-file request.md
magi status --run-id <run-id>
magi resume --run-id <run-id>
magi report --run-id <run-id>
```

Do not build a distributed service, HTTP API or scheduler unless needed. Keep separate:
CLI interface / application service (run start & management) / LangGraph engine /
Git & workspace management — so future interfaces don't duplicate orchestration.

## 0.2 Run contract

Typed input, at least: target repository locator; requested revision; resolved starting
commit; original request; logical project identity; authorized policy profile; budget;
agent configuration; result publication mode. No tokens/credentials in persisted locators.

Typed output, at least: run_id; target identity; starting commit; final commit or artifact;
plan version; terminal status; verification report; references to diff, docs and evidence;
publication operations actually performed.

SUCCESS must never implicitly mean push, merge or deployment.

## 0.3 Configuration in the target repository

Optional `.magi/config.yaml` in the target. May describe: languages and project layout;
code and docs paths; build/test/lint commands; generated files; required modification
scopes; documentation conventions; services to start for tests.

Project config is NOT a source of authorization. It cannot: disable mandatory gates;
raise agent permissions; enable secret access; authorize destructive commands; change
platform policy; enable network, push, merge or deploy by itself.

Precedence: platform policy > authorized run profile > project config > agent inferences.

Validate with a typed schema. If absent: run project discovery, record inferred settings,
never declare passed any check you cannot execute. Project-defined commands are untrusted
input: run them only through the runner and its authorized limits.

## 0.4 Isolation and Git management

`WorkspaceManager` responsibilities: acquire the authorized repo; resolve starting commit;
prepare a run-dedicated workspace; record branch, commits, patches; manage the
DEV1 writer → DEV2 pair sequence; keep artifacts needed for resume; configurable
retention and cleanup.

A separate clone/worktree isolates Git changes but is NOT a security boundary for code
execution. Separate:
- `WorkspaceManager`: filesystem and Git.
- `ExecutionRunner`: command execution with limits and isolation.

Provide a container execution backend for target build/test. Never mount into the
container: the control repo writable; caller credentials; unneeded personal directories;
the container runtime socket. A local runner for development is allowed but must be
explicit and documented as NOT equivalent to a sandbox.

Agents cannot pick an arbitrary workspace or access other runs' repositories.

## 0.5 Authentication and publication

Git credentials are handled by the infrastructure layer. Never put them in prompts, tool
outputs, checkpoints, reports, or persisted Git URLs.

v1 produces: a local Git candidate and/or exportable patch; a structured report;
verification evidence.

Push, PR creation, merge, deploy: disabled by default; outside agents' decision power;
only executable by adapters with external authorization and explicit policy.

Core must not depend on GitHub: support any Git repo without assuming a hosting provider.

## 0.6 Versioning and reproducibility

Per run record: magi-system version/commit; prompt versions; policy version; effective
agent config (no secrets); target starting commit; project config used; execution
environment identifier; final candidate; evidence and check results.

Resume must check compatibility with run artifacts. Never silently resume with different
prompts or policies. Evidence must be bound to the correct target and candidate, not just
a generic run_id.

## 0.7 Multi-repository tests (in addition to section 14)

1. Run on a fixture repository external to magi-system.
2. Original checkout untouched.
3. Control repository untouched by agents.
4. Isolation between two projects and two concurrent runs.
5. Invalid Git revision.
6. Inaccessible target.
7. `.magi` config missing or malformed.
8. Target attempts to disable a gate.
9. Attempt to run an unauthorized command.
10. Resume with missing workspace or different candidate.
11. No credentials in logs, checkpoints, reports.
12. Patch/report generation without push.
13. Uncommitted local changes rejected unless explicit option.
14. Run on a target whose stack differs from magi-system's.

The end-to-end demo must create a temporary target repository distinct from magi-system
and work on that.
