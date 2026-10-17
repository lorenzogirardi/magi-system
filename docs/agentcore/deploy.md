# AgentCore deploy runbook

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md) · [Observability](observability.md)

Requires explicit approval: it creates billable AWS resources.

## What changes what: image, code, settings

| Layer | Holds | Changes | How to deploy |
|---|---|---|---|
| Base image | Python, git, uv, the locked dependencies, `bootstrap.py` (no magi-system code) | rarely: `pyproject.toml` / `uv.lock` / `Dockerfile` | build + push + `terraform apply` (below) |
| Code bundle | `src/magi_system` (code, prompts, policy) | every change | `deploy/agentcore/push_code.py`: seconds, no Docker, no apply |
| Run settings | budget, models, `sandbox.*`, repo, request | every run | the `start` payload |
| Infrastructure | VPC, EFS, IAM, dashboard | almost never | Terraform |

```bash
uv run --extra agentcore python deploy/agentcore/push_code.py              # publish and point new sessions to it
uv run --extra agentcore python deploy/agentcore/push_code.py --dry-run    # version and size only
uv run --extra agentcore python deploy/agentcore/push_code.py --rollback <version>
```

The bundle is a reproducible zip of `src/magi_system` (about 100 KB) at `s3://<code bucket>/code/<version>.zip`
with a manifest (`sha256`, commit, hash of the locked dependencies) and a pointer `code/current.json`. A microVM's
`bootstrap.py` reads the pointer, verifies the sha256, unpacks the bundle and starts the app when the **container
starts**. AgentCore keeps idle containers warm (up to 15 minutes) and hands them to new sessions, so right after
`push_code.py` a new run can land on a container that loaded the previous bundle. The report says which code a run
uses (`versions.platform.commit`, as `<commit>@<version>`). `run_and_log.py` checks it a minute after the start;
if the run is not on the published bundle it stops the run before any model call, deletes its record and access
point, and starts again (`--retries`, default 5; each attempt uses up one old container). Starting a run by hand
needs the same check. If the bundle was built with other
locked dependencies than the image has, the bootstrap stops with "rebuild and deploy the image": that is the only
case that needs the image. A resumed run needs the same code (prompts, policy and control-plane fingerprint), so
push the code before starting runs, not while one is meant to be resumed.

First deployment (2026-10-16, account <aws-account-id>, `eu-north-1`): done. The runtime role needs
`elasticfilesystem:DescribeMountTargets` on `*` (AgentCore rejects a policy scoped to the file system
ARN). If an apply is interrupted while the Runtime is `CREATING`, wait for `READY` and run
`terraform import aws_bedrockagentcore_agent_runtime.magi <agentRuntimeId>`.

```bash
# 0. Build the arm64 image (from the repository root) and run the local smoke test
deploy/agentcore/smoke.sh

# 1. Create the infrastructure (fill terraform.tfvars from the example first)
cd deploy/agentcore/terraform
cp terraform.tfvars.example terraform.tfvars   # region (eu-north-1 answers), image_tag
terraform init && terraform plan -out tf.plan   # review the plan before applying
#    The runtime needs the image to exist: first apply only the ECR repository,
terraform apply -target=aws_ecr_repository.agentcore
#    push the image (step 2), then apply the rest:
terraform apply

# 2. Push the image to the ECR repository from the outputs, then publish the code (push_code.py)
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | podman login --username AWS --password-stdin "${REPO%%/*}"
podman build --platform linux/arm64 -f ../Dockerfile -t "$REPO:<tag>" ../../..
podman push "$REPO:<tag>"

# 3. Store the OpenRouter key (value never in files or shell history)
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw openrouter_secret_arn)" \
  --secret-string "$(read -rs k && echo "$k")"

# 4. Start a run (one session per run; session id >= 33 characters)
ARN=$(terraform output -raw agent_runtime_arn)
SID="magi-session-$(uuidgen | tr -d -)"
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --payload "$(jq -n --rawfile r request.md --slurpfile s settings.json \
     '{action:"start", repo:"https://github.com/org/repo.git", ref:"main", request:$r, settings:$s[0]}')" \
  out.json && cat out.json

# 5. Poll and fetch the result (same or any session)
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --payload '{"action":"status","run_id":"<run_id>"}' status.json
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --payload '{"action":"report","run_id":"<run_id>"}' report.json
jq -r .candidate_patch report.json > candidate.patch
```

The VPC is created by this module (default name `vpc-lab-ai-001`, CIDR `10.42.0.0/16`; see
[Configuration](configuration.md)). It needs Terraform >= 1.9.

`settings.json` is the run settings file as JSON (`provider: openrouter`). Remote targets are
cloned through the NAT gateway. The deployment has no Git credentials: only public
repositories work until a credential helper is added (not implemented).

## Stopping the hourly cost

Only the NAT gateway and its Elastic IP are billed by the hour. Between runs:

```bash
deploy/agentcore/nat.sh stop     # remove them (the runtime loses egress)
deploy/agentcore/nat.sh start    # recreate them before the next run
deploy/agentcore/nat.sh status   # list the NAT gateways of the VPC
```

The script applies only those resources (`terraform apply -target`), asks for Terraform's
confirmation (`-auto-approve` skips it) and needs `terraform.tfvars`. To remove everything, use
`terraform destroy`.

## Trying the result of a run

A run ends with a report and a candidate patch; nothing is pushed. To see the result working:

```bash
# 1. Download the report and the patch (status and report can be read from any session)
deploy/agentcore/fetch-result.sh run-20261016-213811            # -> ./magi-results/<run_id>/

# 2. Apply the patch to a fresh clone, run the target's tests, build its Docker image,
#    start it and call the new endpoints
deploy/agentcore/try-candidate.sh \
  --repo https://github.com/<owner>/<target>.git --ref main \
  --patch magi-results/run-20261016-213811/candidate.patch \
  --health-path /api/mgmt/ready \
  --smoke 'GET /api/v1/toolbox/tools' \
  --smoke 'POST /api/v1/toolbox/runs {"tool":"json.validate","input":{"text":"{}"}}' \
  --auth admin:password            # when the endpoints require Basic auth
```

When the run did not end in SUCCESS (for example the gate could not run a check) `fetch-result.sh` also writes
`unverified.patch`, the developers' last commit, and says so loudly. Try it the same way
(`--patch magi-results/<run_id>/unverified.patch`), knowing the gates did not certify it.

`try-candidate.sh` prints a PASS/FAIL summary and exits non-zero if anything failed: the patch
applies to the ref, the tests pass (`--test-cmd`, default `python -m pytest -q`, in a temporary
`uv` environment built from `requirements.txt`), the image builds (`Dockerfile` or
`docker/Dockerfile`, else `--dockerfile`), the container answers on `--health-path`, and each
`--smoke` request returns a status below 400. Add `--env KEY=VALUE` for what the application needs,
`--port` / `--container-port` for the ports (defaults 18000 and 8000), `--keep` to leave the
container running for manual testing, `--no-docker` or `--no-tests` to skip a part. It needs `git`,
`curl` and docker or podman. The candidate code runs on your machine, outside the sandbox: use it
only for runs you trust. Results are written to `magi-results/`, which is git-ignored.

## Starting a run and logging everything to files

`deploy/agentcore/run_and_log.py` starts a run, follows it and writes `magi-results/<run_id>/`:
`run.log` (progress, gate checks, failures, cost), `events.jsonl` (every event), `runtime-errors.log`,
`status.json`, `report.json`, `candidate.patch`, `summary.txt` / `summary.json` (outcome, model cost,
estimated infrastructure cost, gates) and, with `--try-candidate`, `try-candidate.log`. It stops the
runtime session above `--kill-above` USD (default 5) or after `--max-wait` seconds.

```bash
nohup uv run --extra agentcore python deploy/agentcore/run_and_log.py \
  --repo https://github.com/<owner>/<target>.git --request request.md --settings settings.json \
  --try-candidate --try-candidate-args "--health-path /api/mgmt/ready" > /dev/null 2>&1 &
```

## Collecting a run as a case study

```bash
uv run python deploy/agentcore/collect_example.py magi-results/<run_id> --request request.md
```

Creates `examples/<run_id>-agentcore/` like the earlier case-study folders: `request.md` and
`artifacts/` (`events.jsonl`, `report.json`, `report.md` rendered from it, `candidate.patch`, the
run log and summary, and `stats.json` with the effort per agent, node durations, gate verdicts and the
model and estimated infrastructure cost). `article.md` and `training.md` are written by hand from those numbers.
