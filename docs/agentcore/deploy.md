# AgentCore deploy runbook (not executed yet)

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md)

Requires explicit approval: it creates billable AWS resources.

```bash
# 0. Build the arm64 image (from the repository root) and run the local smoke test
deploy/agentcore/smoke.sh

# 1. Create the infrastructure (fill terraform.tfvars from the example first)
cd deploy/agentcore/terraform
cp terraform.tfvars.example terraform.tfvars   # region, image_tag
terraform init && terraform plan -out tf.plan   # review the plan before applying
#    The runtime needs the image to exist: first apply only the ECR repository,
terraform apply -target=aws_ecr_repository.agentcore
#    push the image (step 2), then apply the rest:
terraform apply

# 2. Push the image to the ECR repository from the outputs
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | podman login --username AWS --password-stdin "${REPO%%/*}"
podman build --platform linux/arm64 -f ../Dockerfile --build-arg MAGI_PLATFORM_REF=$(git rev-parse HEAD) -t "$REPO:<tag>" ../../..
podman push "$REPO:<tag>"

# 3. Store the OpenRouter key (value never in files or shell history)
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw openrouter_secret_arn)" \
  --secret-string "$(read -rs k && echo "$k")"

# 4. Start a run (one session per run; session id >= 33 characters)
ARN=$(terraform output -raw agent_runtime_arn)
SID="magi-session-$(uuidgen | tr -d -)"
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --payload "$(jq -n --rawfile r request.md --slurpfile s settings.json \
     '{action:"start", repo:"https://github.com/org/repo.git", ref:"main", request:$r, settings:$s[0]}')" \
  out.json && cat out.json

# 5. Poll and fetch the result (same or any session)
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --payload '{"action":"status","run_id":"<run_id>"}' status.json
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --payload '{"action":"report","run_id":"<run_id>"}' report.json
jq -r .candidate_patch report.json > candidate.patch
```

`settings.json` is the run settings file as JSON (`provider: openrouter`). Remote targets are
cloned through the NAT gateway. The deployment has no Git credentials: only public
repositories work until a credential helper is added (not implemented).
