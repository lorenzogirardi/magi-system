# magi-system on Amazon Bedrock AgentCore — managed services only (no EC2).
# Files only: validated locally with `terraform init -backend=false && terraform validate`.
# See docs/agentcore/deploy.md before any plan/apply.
terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.69, < 7.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge({ project = "magi-system", managed-by = "terraform" }, var.tags)
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}
