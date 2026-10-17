# --- EFS: run workspaces and artifacts (runs_root) ---------------------------------------

resource "aws_efs_file_system" "runs" {
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  tags             = { Name = "${var.name}-runs" }
}

resource "aws_efs_mount_target" "runs" {
  count           = var.az_count
  file_system_id  = aws_efs_file_system.runs.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# Same-AZ mount targets for the isolated subnets are the private ones above (one per AZ);
# EFS allows a single mount target per AZ, reachable from every subnet of the VPC.

# The runtime sees all runs under /magi. Code Interpreter sessions get a per-run access point
# created at run time (CodeInterpreterRunner) rooted at /magi/<run_id>/workspace.
resource "aws_efs_access_point" "runtime" {
  file_system_id = aws_efs_file_system.runs.id
  posix_user {
    uid = var.posix_uid
    gid = var.posix_uid
  }
  root_directory {
    path = "/magi"
    creation_info {
      owner_uid   = var.posix_uid
      owner_gid   = var.posix_uid
      permissions = "0750"
    }
  }
  tags = { Name = "${var.name}-runtime" }
}

# --- DynamoDB: run records/effects and LangGraph checkpoints ------------------------------

resource "aws_dynamodb_table" "runs" {
  name         = "${var.name}-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
  }
}

# Key schema required by langgraph-checkpoint-aws DynamoDBSaver (unified table: PK + SK).
resource "aws_dynamodb_table" "checkpoints" {
  name         = "${var.name}-checkpoints"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"
  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  ttl {
    attribute_name = "ttl"
    enabled        = var.checkpoint_ttl_days > 0
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
  }
}

# --- S3: offload of checkpoints larger than the DynamoDB item limit ----------------------

resource "aws_s3_bucket" "checkpoints" {
  bucket_prefix = "${var.name}-checkpoints-"
}

resource "aws_s3_bucket_public_access_block" "checkpoints" {
  bucket                  = aws_s3_bucket.checkpoints.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "checkpoints" {
  bucket = aws_s3_bucket.checkpoints.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "checkpoints" {
  bucket = aws_s3_bucket.checkpoints.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# --- S3: the magi-system code bundles (deploy/agentcore/push_code.py) ---------------------
# New sessions download code/current.json's bundle at start (bootstrap.py), so a code change needs no
# image build and no apply. Whoever can write here runs code in the orchestrator, which holds the
# OpenRouter key and the runtime role: the bucket is private, versioned and encrypted, the runtime role
# can only read code/*, and writing is for the person deploying (their own AWS credentials).

resource "aws_s3_bucket" "code" {
  bucket_prefix = "${var.name}-code-"
}

resource "aws_s3_bucket_public_access_block" "code" {
  bucket                  = aws_s3_bucket.code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "code" {
  bucket = aws_s3_bucket.code.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "code" {
  bucket = aws_s3_bucket.code.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "code" {
  bucket = aws_s3_bucket.code.id
  versioning_configuration {
    status = "Enabled"
  }
}

# --- ECR: the magi-agentcore image (built from deploy/agentcore/Dockerfile) --------------

resource "aws_ecr_repository" "agentcore" {
  name                 = "${var.name}-agentcore"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

# --- Secrets Manager: OpenRouter API key (value set outside Terraform) -------------------

resource "aws_secretsmanager_secret" "openrouter" {
  name_prefix = "${var.name}/openrouter-api-key-"
  description = "OpenRouter API key for magi-system runs. Set the value with: aws secretsmanager put-secret-value"
}
