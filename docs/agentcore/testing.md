# AgentCore testing

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md) · [Observability](observability.md)

## Local verification (offline)

- `uv run pytest`:
  - `tests/unit/test_store_dynamo.py`: DynamoDB backend under moto, including a full run
    and a crash/resume.
  - `tests/unit/test_execution_codeinterpreter.py`: runner with fake AWS clients.
  - `tests/unit/test_agentcore_entrypoint.py`: dispatch, async tasks, ping, profile and
    provider rules, secret loading.
- `deploy/agentcore/smoke.sh`: builds the arm64 image with podman and runs the real HTTP
  contract (`/ping`, `/invocations`). Checks start → `HealthyBusy` → SUCCESS → `Healthy` →
  report with the patch. It uses the scripted model, SQLite and the LocalRunner. A test
  layer adds `make` for the demo target; the production image does not have it.
- `terraform -chdir=deploy/agentcore/terraform init -backend=false && terraform validate`.

## Spike checklist (before the first real run)

Use the scripted fake model only. Real models only after all items pass.

1. **Code Interpreter**:
   - `executeCommand` returns `structuredContent.exitCode`, `stdout` and `stderr` as the
     runner expects.
   - arm64/x86; whether `git`, `make`, `python3` and `pytest` are present.
   - A toolchain on EFS via `MAGI_CI_TOOLCHAIN_PATH` works.
   - No internet from the isolated subnets.
   - A session sees only its run's access point.
   - A per-session `filesystemConfigurations` mount works.
2. **EFS**:
   - Per-run access point creation with the `magi-run` tag condition.
   - uid 1000 alignment.
   - `git` and `flock` on NFS from both the runtime and the sandbox.
3. **DynamoDB saver**:
   - Checkpoint sizes and S3 offload.
   - `resume` after `StopRuntimeSession` mid-run, from a new session.
4. **Runtime**:
   - `HealthyBusy` keeps a 60-minute fake run alive.
   - Cold start time.
   - Events reach CloudWatch.
   - `MAGI_OPENROUTER_SECRET_ID` loading.
5. **Terraform**:
   - `plan` and `apply` succeed with provider `hashicorp/aws` ≥ 6.69.
   - IAM conditions are accepted.
   - Clean `destroy`.
6. **Cost** of one demo run.
