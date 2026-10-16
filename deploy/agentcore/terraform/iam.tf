# Least privilege. No role here can invoke Amazon Bedrock models: magi-system calls models
# only through OpenRouter (key in Secrets Manager).

locals {
  account   = data.aws_caller_identity.current.account_id
  partition = data.aws_partition.current.partition
  ci_arn    = aws_bedrockagentcore_code_interpreter.sandbox.code_interpreter_arn
}

data "aws_iam_policy_document" "agentcore_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:bedrock-agentcore:${var.region}:${local.account}:*"]
    }
  }
}

# --- Runtime role (the orchestrator) ------------------------------------------------------

resource "aws_iam_role" "runtime" {
  name_prefix        = "${var.name}-runtime-"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.agentcore.arn]
  }
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "RunStore"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:BatchGetItem",
    "dynamodb:BatchWriteItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.runs.arn, aws_dynamodb_table.checkpoints.arn]
  }
  statement {
    sid       = "CheckpointOffload"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:PutObjectTagging"]
    resources = ["${aws_s3_bucket.checkpoints.arn}/*"]
  }
  statement {
    sid       = "CheckpointOffloadLifecycle"
    actions   = ["s3:GetBucketLifecycleConfiguration", "s3:PutBucketLifecycleConfiguration"]
    resources = [aws_s3_bucket.checkpoints.arn]
  }
  statement {
    sid       = "OpenRouterKey"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.openrouter.arn]
  }
  statement {
    sid       = "MountRuns"
    actions   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [aws_efs_file_system.runs.arn]
    condition {
      test     = "ArnEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.runtime.arn]
    }
  }
  # AgentCore validates the filesystem mount of the runtime role with this call; the check is
  # not satisfied by a policy scoped to the file system ARN, so the (read-only) resource is "*".
  statement {
    sid       = "DescribeMountTargets"
    actions   = ["elasticfilesystem:DescribeMountTargets"]
    resources = ["*"]
  }
  # Per-run access points for the sandbox: create only with the magi-run tag, delete only tagged ones.
  statement {
    sid       = "CreateRunAccessPoint"
    actions   = ["elasticfilesystem:CreateAccessPoint", "elasticfilesystem:TagResource"]
    resources = [aws_efs_file_system.runs.arn, "arn:${local.partition}:elasticfilesystem:${var.region}:${local.account}:access-point/*"]
    condition {
      test     = "Null"
      variable = "aws:RequestTag/magi-run"
      values   = ["false"]
    }
  }
  statement {
    sid       = "DeleteRunAccessPoint"
    actions   = ["elasticfilesystem:DeleteAccessPoint"]
    resources = ["arn:${local.partition}:elasticfilesystem:${var.region}:${local.account}:access-point/*"]
    condition {
      test     = "Null"
      variable = "aws:ResourceTag/magi-run"
      values   = ["false"]
    }
  }
  statement {
    sid       = "DescribeAccessPoints"
    actions   = ["elasticfilesystem:DescribeAccessPoints"]
    resources = ["*"]
  }
  statement {
    sid = "Sandbox"
    actions = ["bedrock-agentcore:StartCodeInterpreterSession", "bedrock-agentcore:InvokeCodeInterpreter",
    "bedrock-agentcore:StopCodeInterpreterSession", "bedrock-agentcore:GetCodeInterpreterSession"]
    resources = [local.ci_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:${local.partition}:logs:${var.region}:${local.account}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }
  statement {
    sid       = "Traces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "runtime" {
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

# --- Sandbox role (Code Interpreter): only mount the run's access point -----------------

resource "aws_iam_role" "sandbox" {
  name_prefix        = "${var.name}-sandbox-"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

# Mount only through an access point, never the runtime one (which sees every run). Per-run
# access points are created by the runtime role, which can only create `magi-run`-tagged ones.
data "aws_iam_policy_document" "sandbox" {
  statement {
    sid       = "MountRunWorkspace"
    actions   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [aws_efs_file_system.runs.arn]
    condition {
      test     = "ArnLike"
      variable = "elasticfilesystem:AccessPointArn"
      values   = ["arn:${local.partition}:elasticfilesystem:${var.region}:${local.account}:access-point/*"]
    }
    condition {
      test     = "ArnNotEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.runtime.arn]
    }
  }
}

resource "aws_iam_role_policy" "sandbox" {
  role   = aws_iam_role.sandbox.id
  policy = data.aws_iam_policy_document.sandbox.json
}
