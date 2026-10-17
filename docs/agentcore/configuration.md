# AgentCore configuration

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md) · [Observability](observability.md)

Terraform sets all of these on the runtime (`deploy/agentcore/terraform/agentcore.tf`).
`examples/agentcore.env.example` lists them without values.

| Variable | Meaning |
|---|---|
| `MAGI_STORE` | `sqlite` (default, CLI) or `dynamodb` |
| `MAGI_DYNAMODB_RUNS_TABLE` | Runs table: key `pk` (`run#<id>`, `effect#<key>`) |
| `MAGI_DYNAMODB_CHECKPOINT_TABLE` | Checkpoint table: keys `PK` + `SK`, TTL attribute `ttl` |
| `MAGI_CHECKPOINT_S3_BUCKET` | Optional S3 offload for checkpoints over 350 KB |
| `MAGI_CODE_BUCKET` | S3 bucket with the magi-system code bundles; `bootstrap.py` loads `code/current.json` from it at session start (Terraform sets it; unset = import the installed package, for development) |
| `MAGI_CODE_POINTER`, `MAGI_CODE_DIR`, `MAGI_DEPS_HASH_FILE` | Bootstrap only: pointer key (default `code/current.json`), extraction directory (`/tmp/magi-code`), dependency hash baked into the image (`/opt/magi/deps.sha256`) |
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

## Terraform variables

Set in `deploy/agentcore/terraform/terraform.tfvars` (see `terraform.tfvars.example`).
`region` and `image_tag` are required. The network ones:

| Variable | Default | Meaning |
|---|---|---|
| `vpc_name` | `vpc-lab-ai-001` | Name tag of the dedicated VPC. Subnets are `subnet-<pub\|priv\|iso>-00<n>-<vpc_name>` |
| `vpc_cidr` | `10.42.0.0/16` | VPC CIDR (must hold six `/24` subnets) |
| `az_count` | `2` | Availability Zones used |
| `dashboard_cost_cap_usd` | `3` | Cap line on the dashboard cost graph; set it to the run `max_cost_usd` |
| `infra_usd_per_hour` | `0.27` | Estimated AWS cost of one active run hour, for the dashboard ([Observability](observability.md)) |
| `nat_enabled` | `true` | NAT gateway + Elastic IP (hourly cost). Toggle with `deploy/agentcore/nat.sh` |

## Run settings used by AgentCore

Passed as `settings` in the `start` payload (same schema as `--config`, see
`examples/settings.example.yaml`).

| Setting | Meaning |
|---|---|
| `default_agent.price` / `agents.<id>.price` | `input_per_mtok`, `output_per_mtok`, optional `cached_input_per_mtok` (USD per million tokens). When set, the cost of each call is computed from it and used by `budget.max_cost_usd`; the provider figure is still logged as `provider_cost_usd`. OpenRouter only. **Leave it unset unless the list price is what you pay**: with `routing: {sort: throughput}` OpenRouter picks the fastest provider, which can cost more than the model's list price (measured 2.7x on `deepseek/deepseek-v4.1-flash`, 0.55 vs 1.46 USD for the same run). Unset, the budget uses the cost OpenRouter reports. |
| `sandbox.requirements` | Requirements files of the target repository (paths relative to the repo) whose wheels are installed for the Code Interpreter. See below. |
| `sandbox.packages` | Extra packages installed the same way, e.g. `["flake8"]` when the repository's gates call a tool its requirements do not list. Plain specifiers only. |

### Sandbox toolchain

The Code Interpreter has no internet, no `git`, no `pytest` and Python 3.12 only under
`/opt/amazon/genesis1p-tools/venv/bin` (aarch64). With `sandbox.requirements: [requirements.txt]`
the runtime, which has egress through the NAT gateway, runs `uv pip install` with
`--only-binary :all: --python-platform aarch64-manylinux_2_28 --python-version 3.12` into
`<run workspace>/.git/magi-toolchain/site` (plus `pytest`), before any agent runs. The sandbox runs
commands with `PYTHONPATH=/mnt/ws/.git/magi-toolchain/site` and the Amazon Python first in `PATH`
(`MAGI_CI_TOOLCHAIN_PATH`, Terraform `ci_toolchain_path`). Wheels only, so nothing executes at
install time; requirement lines that are options, URLs or paths are rejected, because the file comes
from the target repository. The install is skipped when the files are unchanged and is excluded from
git (`.git/info/exclude`). A failure ends the run as `START_FAILED`, with the installer output,
before any model call. Limits: Python and pip only; a dependency without an aarch64 / Python 3.12
wheel fails the install.

Install with `uv sync --extra agentcore`. The extra adds `bedrock-agentcore`, `boto3` and
`langgraph-checkpoint-aws`. The entrypoint is the `magi-agentcore` console script, which
listens on port 8080.
