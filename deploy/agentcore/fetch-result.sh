#!/usr/bin/env bash
# Download the report and the candidate patch of a run from the deployed AgentCore runtime.
#
#   deploy/agentcore/fetch-result.sh RUN_ID [OUT_DIR]
#
# Writes OUT_DIR/RUN_ID/{status.json,report.json,candidate.patch,unverified.patch} (OUT_DIR defaults to
# ./magi-results, which is git-ignored) and prints the status and the cost summary. Any session
# can answer `status` and `report`: they read DynamoDB and EFS. Then try the result with
# deploy/agentcore/try-candidate.sh. Needs aws, jq, terraform (for the runtime ARN).
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: $0 RUN_ID [OUT_DIR]" >&2; exit 2; }
run_id="$1"
out="${2:-./magi-results}/$run_id"
here="$(cd "$(dirname "$0")" && pwd)"
arn="${MAGI_RUNTIME_ARN:-$(terraform -chdir="$here/terraform" output -raw agent_runtime_arn)}"
region="$(cut -d: -f4 <<<"$arn")"
mkdir -p "$out"

invoke() {  # invoke ACTION OUTFILE
  aws bedrock-agentcore invoke-agent-runtime --region "$region" --agent-runtime-arn "$arn" \
    --runtime-session-id "magi-session-fetch-$(uuidgen | tr -d -)" --cli-binary-format raw-in-base64-out \
    --payload "{\"action\":\"$1\",\"run_id\":\"$run_id\"}" "$2" >/dev/null
}

invoke status "$out/status.json"
jq -e 'has("error") | not' "$out/status.json" >/dev/null || { jq . "$out/status.json" >&2; exit 1; }
invoke report "$out/report.json"
jq -e 'has("error") | not' "$out/report.json" >/dev/null || { jq . "$out/report.json" >&2; exit 1; }
jq -j '.candidate_patch // ""' "$out/report.json" >"$out/candidate.patch"      # -j: no trailing newline, so an empty patch is an empty file
jq -j '.unverified_patch // ""' "$out/report.json" >"$out/unverified.patch"

echo "run:    $run_id"
echo "status: $(jq -r '.status' "$out/status.json")"
jq -r '.report.outcome | "outcome: \(.status // "?")  final_commit: \(.final_commit // "-")"' "$out/report.json" 2>/dev/null || true
if [ "$(jq -r '.patch_truncated' "$out/report.json")" = "true" ]; then
  echo "WARNING: the patch was truncated at 5 MB; the full one is on EFS" >&2
fi
if [ -s "$out/candidate.patch" ]; then
  echo "patch:  $out/candidate.patch ($(wc -l <"$out/candidate.patch" | tr -d ' ') lines)"
  git apply --stat "$out/candidate.patch" 2>/dev/null | tail -n 12 || true
else
  echo "patch:  none (the run produced no candidate)"
fi
if [ -s "$out/unverified.patch" ]; then
  echo "UNVERIFIED patch: $out/unverified.patch ($(wc -l <"$out/unverified.patch" | tr -d ' ') lines)"
  echo "  the developers' last commit; the gates did NOT certify it: $(jq -r '.report.unverified_candidate.diagnosis // ""' "$out/report.json" | cut -c1-200)"
  echo "  try it with: deploy/agentcore/try-candidate.sh --patch $out/unverified.patch ..."
fi
echo "files:  $out"
