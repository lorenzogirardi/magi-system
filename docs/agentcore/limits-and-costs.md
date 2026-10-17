# AgentCore limits and costs

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md) · [Observability](observability.md)

## Limits

- One sandbox command can run for at most **840 s**: a synchronous invocation is capped at
  15 minutes and the runner keeps a margin. A longer command timeout is capped.
- Code Interpreter has **no custom images**. Python dependencies are installed as wheels into the
  run workspace (`sandbox.requirements`, see [Configuration](configuration.md)). The target's toolchain (`make`, pytest, project
  dependencies) must be available in the sandbox: preinstalled tools, or installed on EFS and
  exposed with `MAGI_CI_TOOLCHAIN_PATH`. Confirming this is spike item 1. If it does not work,
  the managed fallback is **AWS CodeBuild** (custom images, EFS mount, no EC2) for gate
  commands. That fallback is designed but not built.
- microVM limits: 2 vCPU / 8 GB, 8 h per session (a new session can resume), arm64 image.
- One run per session. Parallel runs need parallel sessions.
- EFS is NFS. Git and `flock` (the run lock) on EFS must be confirmed by the spike.
- **Single NAT gateway** in one Availability Zone, to limit cost: if that zone fails, the
  runtime loses egress (OpenRouter, Git) until it recovers. For HA add one NAT gateway per
  zone and one private route table per zone in `deploy/agentcore/terraform/network.tf`.

## Costs (orders of magnitude, check current pricing)

- **NAT gateway** (and its Elastic IP): hourly charge plus per-GB processed. This is the only
  resource billed by the hour, so the only one worth switching off between runs:
  `deploy/agentcore/nat.sh stop` / `start` / `status` (Terraform variable `nat_enabled`).
  While stopped the runtime has no egress (OpenRouter, Git); the isolated sandbox is unaffected.
  A full teardown is `terraform destroy`.
- **EFS** (elastic throughput), **Secrets Manager** (per secret per month), **ECR** (storage) and **DynamoDB** (on-demand): pay per use; small for a few runs.
- **AgentCore Runtime and Code Interpreter**: consumption-based (CPU and memory while active).
- **OpenRouter**: as today, bounded by `max_cost_usd` in the run settings.
