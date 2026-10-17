output "agent_runtime_arn" {
  description = "ARN to pass to `aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn`."
  value       = aws_bedrockagentcore_agent_runtime.magi.agent_runtime_arn
}

output "ecr_repository_url" {
  description = "Push the magi-agentcore image here (tag = var.image_tag)."
  value       = aws_ecr_repository.agentcore.repository_url
}

output "openrouter_secret_arn" {
  description = "Set the OpenRouter key with `aws secretsmanager put-secret-value --secret-id <this>`."
  value       = aws_secretsmanager_secret.openrouter.arn
}

output "code_interpreter_id" {
  description = "Custom Code Interpreter used as the command sandbox."
  value       = aws_bedrockagentcore_code_interpreter.sandbox.code_interpreter_id
}

output "efs_file_system_id" {
  description = "EFS file system holding run workspaces and artifacts (/magi/<run_id>)."
  value       = aws_efs_file_system.runs.id
}

output "vpc_id" {
  description = "Dedicated VPC (used by deploy/agentcore/nat.sh status)."
  value       = aws_vpc.this.id
}

output "dashboard_url" {
  description = "CloudWatch dashboard with run status, cost, tokens, latency, failures and gate checks."
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.magi.dashboard_name}"
}

output "code_bucket" {
  description = "Bucket for the magi-system code bundles; deploy/agentcore/push_code.py publishes here."
  value       = aws_s3_bucket.code.bucket
}
