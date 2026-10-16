# AgentCore configuration

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md)

Terraform sets all of these on the runtime (`deploy/agentcore/terraform/agentcore.tf`).
`examples/agentcore.env.example` lists them without values.

| Variable | Meaning |
|---|---|
| `MAGI_STORE` | `sqlite` (default, CLI) or `dynamodb` |
| `MAGI_DYNAMODB_RUNS_TABLE` | Runs table: key `pk` (`run#<id>`, `effect#<key>`) |
| `MAGI_DYNAMODB_CHECKPOINT_TABLE` | Checkpoint table: keys `PK` + `SK`, TTL attribute `ttl` |
| `MAGI_CHECKPOINT_S3_BUCKET` | Optional S3 offload for checkpoints over 350 KB |
| `MAGI_CHECKPOINT_TTL_SECONDS` | Optional checkpoint expiry (DynamoDB TTL + S3 lifecycle, managed by the saver) |
| `MAGI_DYNAMODB_ENDPOINT_URL` | Optional endpoint override (tests / DynamoDB Local) |
| `MAGI_RUNS_ROOT` | Runs root; on AgentCore the EFS mount `/mnt/runs` |
| `MAGI_ALLOWED_PROFILES` | Policy profiles callers may request (default `agentcore`) |
| `MAGI_ALLOWED_PROVIDERS` | Model providers runs may use (default `openrouter`) |
| `MAGI_SETTINGS_FILE` | Run settings YAML used when `start` has no `settings` |
| `MAGI_OPENROUTER_SECRET_ID` | Secrets Manager id of the OpenRouter key, read at process start when `OPENROUTER_API_KEY` is unset |
| `MAGI_CI_IDENTIFIER` | Code Interpreter id (sandbox) |
| `MAGI_EFS_FILE_SYSTEM_ID`, `MAGI_EFS_FILE_SYSTEM_ARN` | EFS file system of the runs |
| `MAGI_EFS_RUNS_PATH` | EFS path that `MAGI_RUNS_ROOT` maps to (runtime access point root, `/magi`) |
| `MAGI_EFS_POSIX_UID`, `MAGI_EFS_POSIX_GID` | POSIX identity of every access point (image user: 1000) |
| `MAGI_CI_TOOLCHAIN_PATH` | `PATH` for target commands in the sandbox |
| `MAGI_CI_SESSION_TIMEOUT_SECONDS` | Sandbox session timeout (default 8 h) |
| `MAGI_EVENTS_STDOUT` | `1`: also print redacted events on stdout (CloudWatch) |

Install with `uv sync --extra agentcore`. The extra adds `bedrock-agentcore`, `boto3` and
`langgraph-checkpoint-aws`. The entrypoint is the `magi-agentcore` console script, which
listens on port 8080.
