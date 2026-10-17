# AgentCore observability

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md) · [Observability](observability.md)

The runtime prints every redacted event as one JSON line on stdout (`MAGI_EVENTS_STDOUT=1`).
AgentCore ships stdout to the CloudWatch log group
`/aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT` (created on the first invocation).
`deploy/agentcore/terraform/observability.tf` turns those lines into metrics and a dashboard.
Nothing leaves AWS. Events are redacted before they are printed; payloads, prompts and
secrets are not in the log.

Open the dashboard from the Terraform output `dashboard_url` (name `<name>-runs`). Pick a time
range that covers the run.

## Dashboard

| Widget | Source | Answers |
|---|---|---|
| Cost (USD): model vs infrastructure | `CostUsd` (blue), `ActiveSeconds` x `infra_usd_per_hour` (orange, estimate), total (grey), cap line at `dashboard_cost_cap_usd` | How much the run costs in model calls and in AWS, and how close it is to the cap |
| Tokens | `InputTokens`, `OutputTokens` | Volume per minute |
| LLM calls and latency | `LlmCalls`, `LlmLatencyMs` p95 | Is the model slow or stuck |
| Run outcomes | `RunEnded` by `Status` | SUCCESS, BLOCKED, BUDGET_EXHAUSTED, INFRA_FAILURE... |
| Failures | `AgentFailure` by `Failure`, `ToolErrors`, `InfraError`, `PolicyDenied` | What is going wrong |
| Gates and findings | `GateCheck` by `Verdict`, `Finding` by `Severity` | Quality of the result |
| Cost and tokens per agent | Logs Insights on `llm_call` | Which agent spends the most |
| Selected run: model cost | Logs Insights on `llm_call` | Computed cost, the provider-reported cost, tokens and latency of the run |
| Selected run: infrastructure cost | Logs Insights on `heartbeat` | Active seconds of the run and the estimated AWS cost |
| Cost per run | Logs Insights on `llm_call` by `run_id` | Spend of each run |
| Progress: nodes | `node_start`, `node_end`, `run_start`, `run_end` | Where the run is now |
| Failures and budget | `agent_failure`, `infra_error`, `budget_exhausted`... | Why it stopped |
| Gate checks | `check`, `gate` | Which command failed, with its exit code |
| Not an event | Lines that are not JSON | Tracebacks and startup errors |

Metrics live in the namespace `Magi/Runs`. Dimensions are only low-cardinality fields
(`Status`, `Failure`, `Verdict`, `Severity`, `Dimension`); `run_id` is never a dimension.
Per-run detail is in the Logs Insights widgets, or query the log group by `run_id`.

## Filter by run

The dashboard has a text variable at the top, "Run id". Type a run id such as
`run-20261016-203939`, or a regular expression (`run-20261016.*`); the default `.*` shows every
run. It filters the Logs Insights tables (cost, progress, failures, gate checks). The metric
graphs cannot be filtered by run: `run_id` is deliberately not a metric dimension.

## Infrastructure cost (an estimate)

AWS does not bill per run, so the dashboard estimates it: while a run is active the runtime prints a
`heartbeat` line every 60 s (`{"kind": "heartbeat", "run_id": ..., "interval_s": 60}`), the metric
`ActiveSeconds` sums them, and the cost is `active hours x infra_usd_per_hour`. The default
0.27 USD/h (`eu-north-1` list prices from the AWS Pricing API) is the NAT gateway (0.046) and its
public IPv4 (0.005), plus the Runtime and the Code Interpreter at an assumed 1 vCPU and 2 GB each
(2 x (0.0895 + 2 x 0.00945)). Both services are billed on consumption, so the real figure
differs. Excluded: EFS, DynamoDB, S3, ECR, Secrets Manager and CloudWatch (small), and the NAT
hours while no run is active. Set `infra_usd_per_hour` in `terraform.tfvars` to change the rate.

## The `llm_call` event

One event per model call: `agent`, `purpose`, `round`, `prompt`, `input_tokens`,
`output_tokens`, `cost_usd`, `provider_cost_usd` and `latency_ms`. `cost_usd` is what the budget
counts: computed from the configured `price` of the model when there is one (see
[Configuration](configuration.md)), otherwise the figure OpenRouter reports. `provider_cost_usd`
is always the figure OpenRouter reports, so the two can be compared. Without a `price` the two are
equal, and that is the recommended setting: the list price underestimated a real run by 2.7x. Cost and tokens are null when
the provider does not report them, and then they are not counted.

## Notes

- Metric filters only count log lines written after they exist, so deploy the dashboard before a run.
- The log group has no retention (AgentCore creates it). Set one if the logs should expire.
- AgentCore's own metrics (invocations, sessions) are not on this dashboard; the event metrics
  are what describe a run.
