# Observability: CloudWatch metrics and a dashboard built from the redacted JSON events the
# runtime prints on stdout (MAGI_EVENTS_STDOUT=1). Nothing is sent to an external service.
# The log group is created by AgentCore on the first invocation of the runtime.

locals {
  log_group    = "/aws/bedrock-agentcore/runtimes/${aws_bedrockagentcore_agent_runtime.magi.agent_runtime_id}-DEFAULT"
  metric_space = "Magi/Runs"

  # name => { pattern, value, unit, dimensions }. Dimensions are low-cardinality fields only
  # (never run_id, never free text).
  metric_filters = {
    LlmCalls      = { pattern = "{ $.kind = \"llm_call\" }", value = "1", unit = "Count", dimensions = {} }
    InputTokens   = { pattern = "{ $.kind = \"llm_call\" && $.input_tokens = * }", value = "$.input_tokens", unit = "Count", dimensions = {} }
    OutputTokens  = { pattern = "{ $.kind = \"llm_call\" && $.output_tokens = * }", value = "$.output_tokens", unit = "Count", dimensions = {} }
    CostUsd       = { pattern = "{ $.kind = \"llm_call\" && $.cost_usd = * }", value = "$.cost_usd", unit = "None", dimensions = {} }
    LlmLatencyMs  = { pattern = "{ $.kind = \"llm_call\" && $.latency_ms = * }", value = "$.latency_ms", unit = "Milliseconds", dimensions = {} }
    ActiveSeconds = { pattern = "{ $.kind = \"heartbeat\" && $.interval_s = * }", value = "$.interval_s", unit = "Seconds", dimensions = {} }
    RunStarted    = { pattern = "{ $.kind = \"run_start\" }", value = "1", unit = "Count", dimensions = {} }
    RunEnded      = { pattern = "{ $.kind = \"run_end\" && $.status = * }", value = "1", unit = "Count", dimensions = { Status = "$.status" } }
    AgentFailure  = { pattern = "{ $.kind = \"agent_failure\" && $.failure = * }", value = "1", unit = "Count", dimensions = { Failure = "$.failure" } }
    GateCheck     = { pattern = "{ $.kind = \"check\" && $.verdict = * }", value = "1", unit = "Count", dimensions = { Verdict = "$.verdict" } }
    ToolCalls     = { pattern = "{ $.kind = \"tool_call\" }", value = "1", unit = "Count", dimensions = {} }
    ToolErrors    = { pattern = "{ $.kind = \"tool_call\" && $.ok = false }", value = "1", unit = "Count", dimensions = {} }
    Finding       = { pattern = "{ $.kind = \"finding\" && $.severity = * }", value = "1", unit = "Count", dimensions = { Severity = "$.severity" } }
    BudgetHit     = { pattern = "{ $.kind = \"budget_exhausted\" && $.dimension = * }", value = "1", unit = "Count", dimensions = { Dimension = "$.dimension" } }
    InfraError    = { pattern = "{ $.kind = \"infra_error\" }", value = "1", unit = "Count", dimensions = {} }
    PolicyDenied  = { pattern = "{ $.kind = \"policy_violation\" }", value = "1", unit = "Count", dimensions = {} }
  }

  insights = "SOURCE '${local.log_group}' | "
}

resource "aws_cloudwatch_log_metric_filter" "events" {
  for_each       = local.metric_filters
  name           = "${var.name}-${each.key}"
  log_group_name = local.log_group
  pattern        = each.value.pattern

  metric_transformation {
    name       = each.key
    namespace  = local.metric_space
    value      = each.value.value
    unit       = each.value.unit
    dimensions = length(each.value.dimensions) == 0 ? null : each.value.dimensions
  }
}

resource "aws_cloudwatch_dashboard" "magi" {
  dashboard_name = "${var.name}-runs"
  dashboard_body = jsonencode({
    variables = [{
      type  = "pattern", pattern = "RUNFILTER", inputType = "input", id = "runfilter"
      label = "Run id (regex; .* = all runs)", defaultValue = ".*", visible = true
    }]
    widgets = [
      {
        type       = "text", x = 0, y = 0, width = 24, height = 2
        properties = { markdown = "# magi-system runs\nMetrics come from the redacted event log of the AgentCore runtime (`${local.log_group}`). Cost is reported by OpenRouter per call; the run stops at `max_cost_usd`. Select a time range that covers the run, and type a run id in the variable above to filter the tables." }
      },
      {
        type = "metric", x = 0, y = 2, width = 8, height = 6
        properties = {
          title = "Cost (USD): model vs infrastructure (estimate)", region = var.region, view = "timeSeries", stat = "Sum", period = 60
          metrics = [
            [local.metric_space, "CostUsd", { id = "m1", visible = false }],
            [local.metric_space, "ActiveSeconds", { id = "m2", visible = false }],
            [{ expression = "RUNNING_SUM(FILL(m1, 0))", id = "e1", label = "model", color = "#1f77b4" }],
            [{ expression = "RUNNING_SUM(FILL(m2, 0)) / 3600 * ${var.infra_usd_per_hour}", id = "e2", label = "infrastructure (estimate)", color = "#ff7f0e" }],
            [{ expression = "e1 + e2", id = "e3", label = "total", color = "#7f7f7f" }],
          ]
          annotations = { horizontal = [{ label = "dashboard cap", value = var.dashboard_cost_cap_usd }] }
          yAxis       = { left = { min = 0 } }
        }
      },
      {
        type = "metric", x = 8, y = 2, width = 8, height = 6
        properties = {
          title = "Tokens", region = var.region, view = "timeSeries", stat = "Sum", period = 60
          metrics = [
            [local.metric_space, "InputTokens", { label = "input" }],
            [local.metric_space, "OutputTokens", { label = "output" }],
          ]
        }
      },
      {
        type = "metric", x = 16, y = 2, width = 8, height = 6
        properties = {
          title = "LLM calls and latency", region = var.region, view = "timeSeries", period = 60
          metrics = [
            [local.metric_space, "LlmCalls", { stat = "Sum", label = "calls" }],
            [local.metric_space, "LlmLatencyMs", { stat = "p95", label = "latency p95 (ms)", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 8, width = 8, height = 6
        properties = {
          title   = "Run outcomes", region = var.region, view = "bar", stat = "Sum", period = 3600, setPeriodToTimeRange = true
          metrics = [[{ expression = "SEARCH('{${local.metric_space},Status} MetricName=\"RunEnded\"', 'Sum', 3600)", id = "s1" }]]
        }
      },
      {
        type = "metric", x = 8, y = 8, width = 8, height = 6
        properties = {
          title = "Agent failures, tool errors, infra errors", region = var.region, view = "timeSeries", stat = "Sum", period = 60
          metrics = [
            [{ expression = "SEARCH('{${local.metric_space},Failure} MetricName=\"AgentFailure\"', 'Sum', 60)", id = "s2" }],
            [local.metric_space, "ToolErrors", { label = "tool errors" }],
            [local.metric_space, "InfraError", { label = "infra errors" }],
            [local.metric_space, "PolicyDenied", { label = "policy denied" }],
          ]
        }
      },
      {
        type = "metric", x = 16, y = 8, width = 8, height = 6
        properties = {
          title = "Gate checks by verdict and findings by severity", region = var.region, view = "timeSeries", stat = "Sum", period = 60
          metrics = [
            [{ expression = "SEARCH('{${local.metric_space},Verdict} MetricName=\"GateCheck\"', 'Sum', 60)", id = "s3" }],
            [{ expression = "SEARCH('{${local.metric_space},Severity} MetricName=\"Finding\"', 'Sum', 60)", id = "s4" }],
          ]
        }
      },
      {
        type = "log", x = 0, y = 14, width = 14, height = 6
        properties = {
          title = "Selected run: model cost (computed) vs provider-reported", region = var.region, view = "table"
          query = "${local.insights}filter kind = \"llm_call\" and run_id like /RUNFILTER/ | stats sum(cost_usd) as model_cost_usd, sum(provider_cost_usd) as provider_reported_usd, count(*) as llm_calls, sum(input_tokens) as in_tokens, sum(output_tokens) as out_tokens, avg(latency_ms) as avg_ms by run_id | sort model_cost_usd desc | limit 20"
        }
      },
      {
        type = "log", x = 14, y = 14, width = 10, height = 6
        properties = {
          title = "Selected run: infrastructure cost (estimate)", region = var.region, view = "table"
          query = "${local.insights}filter kind = \"heartbeat\" and run_id like /RUNFILTER/ | stats sum(interval_s) as active_seconds, sum(interval_s) / 3600 * ${var.infra_usd_per_hour} as infra_cost_usd by run_id | sort active_seconds desc | limit 20"
        }
      },
      {
        type = "log", x = 0, y = 20, width = 12, height = 7
        properties = {
          title = "Cost and tokens per agent", region = var.region, view = "table"
          query = "${local.insights}filter kind = \"llm_call\" and run_id like /RUNFILTER/ | stats sum(cost_usd) as cost_usd, sum(provider_cost_usd) as provider_usd, sum(input_tokens) as in_tokens, sum(output_tokens) as out_tokens, count(*) as calls, avg(latency_ms) as avg_ms by agent | sort cost_usd desc"
        }
      },
      {
        type = "log", x = 12, y = 20, width = 12, height = 7
        properties = {
          title = "Cost per run", region = var.region, view = "table"
          query = "${local.insights}filter kind = \"llm_call\" | stats sum(cost_usd) as cost_usd, count(*) as calls, min(@timestamp) as first_call, max(@timestamp) as last_call by run_id | sort first_call desc | limit 20"
        }
      },
      {
        type = "log", x = 0, y = 27, width = 12, height = 8
        properties = {
          title = "Progress: nodes", region = var.region, view = "table"
          query = "${local.insights}filter kind in [\"node_start\", \"node_end\", \"run_start\", \"run_end\"] and run_id like /RUNFILTER/ | fields @timestamp, run_id, kind, node, round, status | sort @timestamp desc | limit 60"
        }
      },
      {
        type = "log", x = 12, y = 27, width = 12, height = 8
        properties = {
          title = "Failures and budget", region = var.region, view = "table"
          query = "${local.insights}filter kind in [\"agent_failure\", \"infra_error\", \"budget_exhausted\", \"policy_violation\", \"run_crashed\", \"technical_retry\"] and run_id like /RUNFILTER/ | fields @timestamp, run_id, kind, agent, node, failure, dimension, detail | sort @timestamp desc | limit 60"
        }
      },
      {
        type = "log", x = 0, y = 35, width = 12, height = 8
        properties = {
          title = "Gate checks", region = var.region, view = "table"
          query = "${local.insights}filter kind in [\"check\", \"gate\"] and run_id like /RUNFILTER/ | fields @timestamp, run_id, kind, gate, verdict, status, exit_code, command | sort @timestamp desc | limit 60"
        }
      },
      {
        type = "log", x = 12, y = 35, width = 12, height = 8
        properties = {
          title = "Runtime output that is not an event (tracebacks, startup)", region = var.region, view = "table"
          query = "${local.insights}filter @message not like /^\\{/ | fields @timestamp, @message | sort @timestamp desc | limit 40"
        }
      },
    ]
  })

  depends_on = [aws_cloudwatch_log_metric_filter.events]
}
