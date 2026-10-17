# Training notes: what the AgentCore runs taught us (run `run-20261017-155113` and the nine before it)

Facts, each with where it was seen. Use as a checklist for the next target.

## Platform behaviour (AgentCore)
- One session = one microVM (2 vCPU / 8 GB), ephemeral disk; keep state on EFS/DynamoDB/S3.
- **Warm containers freeze the code they loaded at start.** Publishing a new code bundle does not replace them;
  new sessions can land on old containers for minutes (this run: 4 of 5 starts). The runtime reports
  `commit@bundle`; `run_and_log.py` compares it with the published pointer, stops a stale start before it
  spends and retries (up to `--retries`). Forcing fresh containers needs a new runtime version (`terraform apply`).
- The Code Interpreter has no internet, no git, no pytest, Python 3.12 aarch64. Wheels are installed by the
  runtime (which has NAT) into `<workspace>/.git/magi-toolchain/site`; `.git/` survives `git clean -fdx` and is
  not seen by `flake8 .`. Commands run with stdin closed (`< /dev/null`) or heredocs hang.
- Cost truth is OpenRouter's `usage.cost`; list-price arithmetic underestimated 2.7x.

## Target onboarding (the cheap check that was missed)
- The target needs a way to run build, test and lint: Makefile targets, package.json scripts, or `.magi/config.yaml`.
  Discovery never invents build/lint. Missing -> the gate is UNKNOWN -> FAILED_VERIFICATION after a full run
  (`run-20261017-144624`, US$ 1.44 for nothing). A preflight at intake now stops this at cost 0.
- `not_applicable` is honoured only for gates the platform policy allows (build, lint, sre, docs) and needs a reason.

## Agent behaviour
- Architects write claims in a typed form. Common mistakes: `symbol_exists` without `path::symbol`,
  policy keys written as sentences, claims about files "after the change". Bare symbol paths are repaired from
  `pattern`; the rest returns to the agent as a retryable error.
- Round 0 can end with no admissible proposal (this run; run 144624 selected in round 0). The default
  `max_architecture_rounds` = 2 allows one revision round; with 1 this run would have ended unresolved.
- QA scenarios written as shell commands: text searches over source (`grep requests`) and guessed `-k`
  selectors give false negatives. Rules: text scans and empty selections are advisory; the QA may correct a
  failed scenario once after seeing the code (verdict still comes from the executed command).
- Output truncation (`max_output_tokens`) hit once (spec.sre, long reasoning); the agent retried and recovered.

## Numbers of this run
116 calls, 5.47 M input / 0.66 M output tokens, US$ 1.35 model + about 0.12 infra, 25 min 44 s.
Claims 359 supported / 77 refuted / 55 unverifiable. Findings 160 (12 major confirmed).
Heaviest roles: magi.casper 0.21, magi.balthasar 0.19, doc.tech_writer 0.14, dev.pair 0.12, dev.writer 0.10.

## Before spending on the next run
1. `uv run pytest` and `ruff` green; `push_code.py` published; note the bundle version.
2. Target has build/test/lint (config file on its default branch).
3. NAT up (`deploy/agentcore/nat.sh start`), stop it afterwards (`nat.sh stop`).
4. Launch with `run_and_log.py` (stale-code guard, `--kill-above`), then `fetch-result.sh` and `try-candidate.sh`.
