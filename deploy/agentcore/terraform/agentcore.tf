# Custom Code Interpreter: the sandbox for every agent and gate command (VPC mode, isolated
# subnets). EFS mounts are attached per session by CodeInterpreterRunner.
resource "aws_bedrockagentcore_code_interpreter" "sandbox" {
  name               = "${var.name}_sandbox"
  description        = "magi-system target command sandbox (no internet; per-run EFS mount)"
  execution_role_arn = aws_iam_role.sandbox.arn
  network_configuration {
    network_mode = "VPC"
    vpc_config {
      subnets         = aws_subnet.isolated[*].id
      security_groups = [aws_security_group.sandbox.id]
    }
  }
}

# The orchestrator: magi-agentcore container on the microVM compute type (serverless).
resource "aws_bedrockagentcore_agent_runtime" "magi" {
  agent_runtime_name = "${var.name}_runtime"
  description        = "magi-system orchestrator (LangGraph). Models via OpenRouter only."
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agentcore.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = "VPC"
    network_mode_config {
      subnets         = aws_subnet.private[*].id
      security_groups = [aws_security_group.runtime.id]
    }
  }

  filesystem_configuration {
    efs_access_point {
      access_point_arn = aws_efs_access_point.runtime.arn
      mount_path       = "/mnt/runs"
    }
  }

  lifecycle_configuration {
    idle_runtime_session_timeout = 900
    max_lifetime                 = var.max_session_seconds
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  environment_variables = merge({
    AWS_REGION                      = var.region
    MAGI_STORE                      = "dynamodb"
    MAGI_RUNS_ROOT                  = "/mnt/runs"
    MAGI_DYNAMODB_RUNS_TABLE        = aws_dynamodb_table.runs.name
    MAGI_DYNAMODB_CHECKPOINT_TABLE  = aws_dynamodb_table.checkpoints.name
    MAGI_CHECKPOINT_S3_BUCKET       = aws_s3_bucket.checkpoints.bucket
    MAGI_CHECKPOINT_TTL_SECONDS     = var.checkpoint_ttl_days > 0 ? tostring(var.checkpoint_ttl_days * 86400) : ""
    MAGI_OPENROUTER_SECRET_ID       = aws_secretsmanager_secret.openrouter.arn
    MAGI_ALLOWED_PROFILES           = "agentcore"
    MAGI_ALLOWED_PROVIDERS          = "openrouter"
    MAGI_CI_IDENTIFIER              = aws_bedrockagentcore_code_interpreter.sandbox.code_interpreter_id
    MAGI_EFS_FILE_SYSTEM_ID         = aws_efs_file_system.runs.id
    MAGI_EFS_FILE_SYSTEM_ARN        = aws_efs_file_system.runs.arn
    MAGI_EFS_RUNS_PATH              = "/magi" # EFS path of the runtime access point root (= MAGI_RUNS_ROOT)
    MAGI_EFS_POSIX_UID              = tostring(var.posix_uid)
    MAGI_EFS_POSIX_GID              = tostring(var.posix_uid)
    MAGI_CI_TOOLCHAIN_PATH          = var.ci_toolchain_path
    MAGI_CI_SESSION_TIMEOUT_SECONDS = tostring(var.max_session_seconds)
    MAGI_EVENTS_STDOUT              = "1"
    }, var.run_settings_file == "" ? {} : { MAGI_SETTINGS_FILE = var.run_settings_file }
  )

  depends_on = [aws_efs_mount_target.runs, aws_iam_role_policy.runtime]
}
