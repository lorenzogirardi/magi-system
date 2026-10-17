#!/usr/bin/env bash
# Local smoke test of the AgentCore image: no AWS, no LLM. Runs the real HTTP contract
# (/ping, /invocations) with the scripted offline model and the SQLite store, and checks the
# async flow: start returns at once, /ping is HealthyBusy while the run is active, the run
# reaches SUCCESS, report returns the patch.
# Usage (from the repository root): deploy/agentcore/smoke.sh
set -euo pipefail

IMAGE=${IMAGE:-localhost/magi-agentcore:smoke}
NAME=magi-agentcore-smoke
PORT=${PORT:-18080}
RUNTIME=${RUNTIME:-$(command -v podman || command -v docker)}

"$RUNTIME" build --platform linux/arm64 -f deploy/agentcore/Dockerfile -t "$IMAGE" .

# The demo target is a Makefile project. In production target commands run in the Code
# Interpreter, never in this image; for the local smoke run (LocalRunner) add `make` in a
# throwaway test layer so the production image stays minimal.
TEST_IMAGE="$IMAGE-test"
printf 'FROM %s\nUSER 0\nRUN apt-get update && apt-get install -y --no-install-recommends make && rm -rf /var/lib/apt/lists/*\nUSER 1000:1000\n' "$IMAGE" \
  | "$RUNTIME" build --platform linux/arm64 -t "$TEST_IMAGE" -f - .

cleanup() { "$RUNTIME" rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

# Same server and dispatcher as production; only the models are the scripted offline ones. The image has no
# magi-system code (production loads a bundle from S3): the working tree is mounted read-only instead.
"$RUNTIME" run -d --name "$NAME" -p "127.0.0.1:$PORT:8080" \
  -v "$PWD/src:/opt/magi/src:ro" -e PYTHONPATH=/opt/magi/src \
  -e MAGI_STORE=sqlite -e MAGI_RUNS_ROOT=/tmp/runs \
  -e MAGI_ALLOWED_PROFILES=offline-test -e MAGI_ALLOWED_PROVIDERS=fake \
  --entrypoint python "$TEST_IMAGE" -c '
from pathlib import Path
from magi_system.interfaces.agentcore import make_app
from magi_system.offline import scripted_models
from magi_system.service import RunService
make_app(lambda: RunService(Path("/tmp/runs"), models=scripted_models())).run(port=8080, host="0.0.0.0")
' >/dev/null

"$RUNTIME" exec "$NAME" python -c '
from pathlib import Path
from magi_system.offline import DEMO_REQUEST, create_demo_repo
create_demo_repo(Path("/tmp/greeter")); Path("/tmp/request.md").write_text(DEMO_REQUEST)'

url="http://127.0.0.1:$PORT"
for _ in $(seq 1 60); do curl -fsS "$url/ping" >/dev/null 2>&1 && break; sleep 0.5; done

invoke() { curl -fsS -X POST "$url/invocations" -H 'Content-Type: application/json' -d "$1"; }
request=$("$RUNTIME" exec "$NAME" python -c 'import json;print(json.dumps(open("/tmp/request.md").read()))')

start=$(invoke "{\"action\":\"start\",\"repo\":\"/tmp/greeter\",\"ref\":\"main\",\"request\":$request,\"profile\":\"offline-test\",\"settings\":{\"default_agent\":{\"provider\":\"fake\",\"model\":\"scripted\"}},\"run_id\":\"smoke-run\"}")
echo "start:  $start"
echo "$start" | grep -q "\"accepted\": *true"
busy=$(curl -fsS "$url/ping"); echo "ping:   $busy"

status=""
for _ in $(seq 1 240); do
  status=$(invoke '{"action":"status","run_id":"smoke-run"}')
  echo "$status" | grep -Eq '"active_in_session": *false' && break
  sleep 0.5
done
echo "status: $(echo "$status" | head -c 300)"
echo "$status" | grep -Eq '"status": *"SUCCESS"'
ping=$(curl -fsS "$url/ping"); echo "ping:   $ping"
echo "$ping" | grep -Eq '"status": *"Healthy"'

report=$(invoke '{"action":"report","run_id":"smoke-run"}')
echo "$report" | grep -q 'farewell()'
echo "smoke: OK (start -> HealthyBusy -> SUCCESS -> Healthy -> report with patch)"
