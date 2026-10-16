#!/usr/bin/env bash
# Start or stop the only hourly-billed resources of the AgentCore deployment: the NAT gateway
# and its Elastic IP. Everything else (EFS, DynamoDB, S3, ECR, Secrets Manager, AgentCore
# Runtime / Code Interpreter) is billed by usage or storage. See docs/agentcore/limits-and-costs.md.
#
#   deploy/agentcore/nat.sh stop    [terraform apply flags]   remove NAT + EIP (no egress)
#   deploy/agentcore/nat.sh start   [terraform apply flags]   recreate them
#   deploy/agentcore/nat.sh status                            show NAT gateways of the VPC
#
# Terraform shows the plan and asks for confirmation; pass -auto-approve to skip it.
# Needs terraform.tfvars in deploy/agentcore/terraform (region, image_tag).
set -euo pipefail

cd "$(dirname "$0")/terraform"
action="${1:-}"
[ $# -gt 0 ] && shift

apply() {
  terraform apply -var "nat_enabled=$1" \
    -target=aws_nat_gateway.this -target=aws_eip.nat -target=aws_route_table.private "${@:2}"
}

case "$action" in
  stop)
    apply false "$@"
    echo "NAT gateway removed: the runtime has no egress (OpenRouter, Git) until 'nat.sh start'."
    echo "Remaining costs are usage/storage based (EFS, DynamoDB, S3, ECR, Secrets Manager)."
    ;;
  start)
    apply true "$@"
    echo "NAT gateway running (billed hourly). Stop it with 'nat.sh stop' when done."
    ;;
  status)
    vpc=$(terraform output -raw vpc_id)
    region=$(terraform console <<<"var.region" | tr -d '"')
    aws ec2 describe-nat-gateways --region "$region" --filter "Name=vpc-id,Values=$vpc" \
      --query 'NatGateways[].[NatGatewayId,State]' --output table
    ;;
  *)
    echo "usage: $0 stop|start|status [terraform apply flags]" >&2
    exit 2
    ;;
esac
