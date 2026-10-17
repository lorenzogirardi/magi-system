#!/usr/bin/env bash
# Try the result of a run: apply the candidate patch to a fresh clone of the target, run its
# tests, build its Docker image, start it, wait until it is up and call the new endpoints.
#
#   deploy/agentcore/try-candidate.sh --repo URL --patch FILE [options]
#
#   --repo URL          target repository (the one the run used)
#   --patch FILE        candidate patch, e.g. magi-results/<run_id>/candidate.patch
#   --ref REF           ref the run started from (default: main)
#   --test-cmd CMD      tests to run on the patched tree (default: python -m pytest -q)
#   --no-tests          skip the tests
#   --dockerfile PATH   Dockerfile in the repository (default: Dockerfile, else docker/Dockerfile)
#   --port N            host port (default: 18000)
#   --container-port N  port the application listens on in the container (default: 8000)
#   --env KEY=VALUE     environment variable for the container (repeatable)
#   --health-path P     path polled until it answers < 500 (default: /)
#   --auth USER:PASS    HTTP Basic credentials for the smoke requests
#   --smoke 'METHOD PATH [JSON_BODY]'
#                       request sent to the running container, e.g. 'GET /api/v1/toolbox/tools'
#                       (repeatable); it passes with an HTTP status below 400
#   --no-docker         skip the image and the smoke requests (tests only)
#   --keep              leave the container running and print its URL
#
# Needs git, curl, and docker or podman; uv is used for the test environment when present.
# The candidate runs on this machine, outside the sandbox: try it only for a run you trust.
set -euo pipefail

repo="" patch="" ref="main" test_cmd="python -m pytest -q" run_tests=1 dockerfile="" port=18000
cport=8000 health="/" auth="" keep=0 use_docker=1
envs=() smokes=()

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --patch) patch="$2"; shift 2 ;;
    --ref) ref="$2"; shift 2 ;;
    --test-cmd) test_cmd="$2"; shift 2 ;;
    --no-tests) run_tests=0; shift ;;
    --dockerfile) dockerfile="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --container-port) cport="$2"; shift 2 ;;
    --env) envs+=("$2"); shift 2 ;;
    --health-path) health="$2"; shift 2 ;;
    --auth) auth="$2"; shift 2 ;;
    --smoke) smokes+=("$2"); shift 2 ;;
    --no-docker) use_docker=0; shift ;;
    --keep) keep=1; shift ;;
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$repo" ] && [ -n "$patch" ] || { echo "--repo and --patch are required (see --help)" >&2; exit 2; }
[ -s "$patch" ] || { echo "patch $patch is missing or empty: the run produced no candidate" >&2; exit 1; }
patch="$(cd "$(dirname "$patch")" && pwd)/$(basename "$patch")"

work="$(mktemp -d "${TMPDIR:-/tmp}/magi-try.XXXXXX")"
cname="magi-try-$$"
engine=""
failures=0
results=()

record() {  # record PASS|FAIL "what"
  results+=("$1  $2")
  [ "$1" = PASS ] || failures=$((failures + 1))
}

cleanup() {
  if [ -n "$engine" ] && [ "$keep" = 0 ]; then "$engine" rm -f "$cname" >/dev/null 2>&1 || true; fi
  [ "$keep" = 1 ] || rm -rf "$work"
}
trap cleanup EXIT

echo "== clone $repo @ $ref"
git clone --quiet "$repo" "$work/src"
git -C "$work/src" checkout --quiet "$ref"
if git -C "$work/src" apply --check "$patch" 2>"$work/apply.err"; then
  git -C "$work/src" apply "$patch"
  git -C "$work/src" add -A
  git -C "$work/src" diff --cached --stat | tail -n 15
  record PASS "patch applies to $ref"
else
  cat "$work/apply.err" >&2
  record FAIL "patch applies to $ref"
  printf '\n== summary\n'; printf '%s\n' "${results[@]}"
  exit 1
fi

if [ "$run_tests" = 1 ]; then
  echo "== tests: $test_cmd"
  (
    cd "$work/src"
    if command -v uv >/dev/null && [ -f requirements.txt ]; then
      uv venv --quiet --python 3.12 "$work/venv" 2>/dev/null || uv venv --quiet "$work/venv"
      uv pip install --quiet --python "$work/venv/bin/python" -r requirements.txt
      export PATH="$work/venv/bin:$PATH"
    fi
    eval "$test_cmd"
  ) && record PASS "tests: $test_cmd" || record FAIL "tests: $test_cmd"
fi

if [ "$use_docker" = 1 ]; then
  engine="$(command -v docker || command -v podman || true)"
  [ -n "$engine" ] || { echo "neither docker nor podman found" >&2; exit 1; }
  engine="$(basename "$engine")"
  "$engine" info >/dev/null 2>&1 || { echo "$engine is not running" >&2; exit 1; }
  if [ -z "$dockerfile" ]; then
    for f in Dockerfile docker/Dockerfile; do [ -f "$work/src/$f" ] && dockerfile="$f" && break; done
  fi
  [ -n "$dockerfile" ] || { echo "no Dockerfile found; pass --dockerfile" >&2; exit 1; }

  echo "== build $dockerfile"
  if "$engine" build --quiet -t "$cname" -f "$work/src/$dockerfile" "$work/src" >"$work/build.log" 2>&1; then
    record PASS "docker build ($dockerfile)"
  else
    tail -n 25 "$work/build.log" >&2
    record FAIL "docker build ($dockerfile)"
    printf '\n== summary\n'; printf '%s\n' "${results[@]}"
    exit 1
  fi

  echo "== run on http://localhost:$port"
  env_args=()
  for e in ${envs[@]+"${envs[@]}"}; do env_args+=(-e "$e"); done
  "$engine" run -d --name "$cname" -p "$port:$cport" ${env_args[@]+"${env_args[@]}"} "$cname" >/dev/null
  up=0
  for _ in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:$port$health" || true)"
    case "$code" in 000|"") ;; *) [ "$code" -lt 500 ] && { up=1; break; } ;; esac
    sleep 1
  done
  if [ "$up" = 1 ]; then
    record PASS "container answers on $health ($code)"
  else
    "$engine" logs --tail 40 "$cname" >&2 || true
    record FAIL "container answers on $health"
  fi

  if [ "$up" = 1 ]; then
    for s in ${smokes[@]+"${smokes[@]}"}; do
      method="${s%% *}"; rest="${s#* }"; path="${rest%% *}"; body=""
      [ "$rest" != "$path" ] && body="${rest#* }"
      curl_args=(-s -o "$work/resp.out" -w '%{http_code}' --max-time 30 -X "$method" "http://localhost:$port$path")
      [ -n "$auth" ] && curl_args+=(-u "$auth")
      [ -n "$body" ] && curl_args+=(-H 'Content-Type: application/json' -d "$body")
      code="$(curl "${curl_args[@]}" || true)"
      echo "-- $method $path -> $code"; head -c 600 "$work/resp.out"; echo
      if [ -n "$code" ] && [ "$code" != 000 ] && [ "$code" -lt 400 ]; then record PASS "$method $path ($code)"; else record FAIL "$method $path ($code)"; fi
    done
  fi
  [ "$keep" = 1 ] && echo "container $cname left running: http://localhost:$port (remove: $engine rm -f $cname)"
fi

printf '\n== summary\n'
printf '%s\n' "${results[@]}"
[ "$failures" = 0 ]
