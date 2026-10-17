#!/usr/bin/env python3
"""Start a run on the deployed AgentCore runtime and log everything to files.

    uv run --extra agentcore python deploy/agentcore/run_and_log.py \\
        --repo https://github.com/<owner>/<target>.git --request request.md --settings settings.json

Run it detached when you do not want to keep a terminal open:

    nohup uv run --extra agentcore python deploy/agentcore/run_and_log.py ... > /dev/null 2>&1 &

Everything goes to <log-dir>/<run_id>/ (log-dir defaults to ./magi-results):
  run.log           timestamped progress: nodes, gate checks, failures, cost every 10 model calls
  events.jsonl      every event of the run, as the runtime printed it
  runtime-errors.log  runtime output that is not an event (tracebacks, startup errors)
  status.json, report.json, candidate.patch   the result (deploy/agentcore/fetch-result.sh)
  summary.txt / summary.json   outcome, diagnosis, model and infrastructure cost, gates
  try-candidate.log  only with --try-candidate: the patch tried on a fresh clone and a Docker build

Safety: the runtime session is stopped if the model cost passes --kill-above USD (the run has its own
budget; this is the backstop) or after --max-wait seconds. A stopped run can be resumed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
ACTIVE = {"STARTING", "RUNNING"}
SHOW = {"run_start", "run_end", "run_crashed", "agent_failure", "infra_error", "budget_exhausted",
        "policy_violation", "technical_retry", "gate", "magi_round", "publication"}


class Log:
    def __init__(self, path: Path) -> None:
        self.fh = path.open("a", buffering=1)

    def __call__(self, msg: str) -> None:
        line = f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")


def tf_output(name: str) -> str:
    out = subprocess.run(["terraform", f"-chdir={HERE / 'terraform'}", "output", "-raw", name], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def runtime_arn(args) -> str:
    return args.runtime_arn or tf_output("agent_runtime_arn")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--request", required=True, help="Markdown file with the change request")
    ap.add_argument("--settings", required=True, help="run settings as JSON (budget, models, sandbox)")
    ap.add_argument("--project", default=None)
    ap.add_argument("--runtime-arn", default=None, help="default: terraform output agent_runtime_arn")
    ap.add_argument("--log-dir", default="./magi-results", help="parent directory; files go to <log-dir>/<run_id>/")
    ap.add_argument("--poll", type=float, default=30.0, help="seconds between polls")
    ap.add_argument("--max-wait", type=float, default=7200.0)
    ap.add_argument("--kill-above", type=float, default=5.0, help="stop the session above this model cost (USD)")
    ap.add_argument("--infra-usd-per-hour", type=float, default=0.27)
    ap.add_argument("--retries", type=int, default=5, help="restarts allowed when the run lands on a container with old code")
    ap.add_argument("--expect-version", default=None, help="code bundle the run must use; default: the one code/current.json points to")
    ap.add_argument("--try-candidate", action="store_true", help="when there is a patch, run try-candidate.sh")
    ap.add_argument("--try-candidate-args", default="", help="extra arguments for try-candidate.sh, one string")
    args = ap.parse_args()

    arn = runtime_arn(args)
    region = arn.split(":")[3]
    agentcore = boto3.client("bedrock-agentcore", region_name=region)
    logs = boto3.client("logs", region_name=region)
    group = f"/aws/bedrock-agentcore/runtimes/{arn.split('/')[-1]}-DEFAULT"

    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    session = f"magi-session-{run_id}-{uuid.uuid4().hex}"
    out = Path(args.log_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    log = Log(out / "run.log")
    events_fh = (out / "events.jsonl").open("a", buffering=1)
    errors_fh = (out / "runtime-errors.log").open("a", buffering=1)

    def invoke(payload: dict) -> dict:
        r = agentcore.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=session,
                                           payload=json.dumps(payload).encode())
        return json.loads(r["response"].read() or b"{}")

    settings = json.loads(Path(args.settings).read_text())
    start_payload = {"action": "start", "repo": args.repo, "ref": args.ref,
                     "request": Path(args.request).read_text(), "settings": settings, "run_id": run_id}
    if args.project:
        start_payload["project"] = args.project
    started = time.time()
    log(f"start {run_id} repo={args.repo} ref={args.ref} runtime={arn.split('/')[-1]} log_dir={out}")
    resp = invoke(start_payload)
    log(f"start response: {json.dumps(resp)}")
    if not resp.get("accepted"):
        log("run not accepted; nothing more to do")
        return 1

    start_ms = int(started * 1000) - 5000
    seen: set[str] = set()
    calls, cost, provider_cost, tok_in, tok_out, active_s = 0, 0.0, 0.0, 0, 0, 0.0
    gates: dict[str, str] = {}
    last_status, dead_polls, killed = "", 0, ""

    def pull_events() -> None:
        nonlocal calls, cost, provider_cost, tok_in, tok_out, active_s
        for pattern, is_event in ((f'{{ $.run_id = "{run_id}" }}', True), ('?Traceback ?CRITICAL ?"Error"', False)):
            token = None
            while True:
                kw = dict(logGroupName=group, startTime=start_ms, filterPattern=pattern)
                if token:
                    kw["nextToken"] = token
                page = logs.filter_log_events(**kw)
                for ev in sorted(page.get("events", []), key=lambda e: e["timestamp"]):
                    if ev["eventId"] in seen:
                        continue
                    seen.add(ev["eventId"])
                    msg = ev["message"].strip()
                    try:
                        rec = json.loads(msg)
                    except ValueError:
                        errors_fh.write(f"{ev['timestamp']} {msg}\n")
                        log(f"RUNTIME {msg[:300]}")
                        continue
                    if not is_event or rec.get("run_id") != run_id or "kind" not in rec:
                        continue
                    events_fh.write(json.dumps(rec, sort_keys=True) + "\n")
                    kind = rec["kind"]
                    if kind == "llm_call":
                        calls += 1
                        cost += rec.get("cost_usd") or 0
                        provider_cost += rec.get("provider_cost_usd") or 0
                        tok_in += rec.get("input_tokens") or 0
                        tok_out += rec.get("output_tokens") or 0
                        if calls % 10 == 0:
                            log(f"PROGRESS llm_calls={calls} cost=${cost:.3f} tokens={tok_in}+{tok_out}")
                    elif kind == "heartbeat":
                        active_s += rec.get("interval_s") or 0
                    elif kind == "node_start":
                        log(f"NODE {rec.get('node')} (round {rec.get('round')})  [calls={calls} cost=${cost:.3f}]")
                    elif kind == "check":
                        gates[rec.get("gate", "?")] = rec.get("verdict", "?")
                        log(f"CHECK {rec.get('gate')} {rec.get('verdict')} exit={rec.get('exit_code')} {rec.get('command', '')}")
                    elif kind in SHOW:
                        keep = {k: v for k, v in rec.items() if k not in ("ts", "run_id", "kind", "policy", "prompts", "checks")}
                        log(f"{kind.upper()} {json.dumps(keep, default=str)[:400]}")
                token = page.get("nextToken")
                if not token:
                    break

    def stop_session(reason: str) -> None:
        log(f"stopping the runtime session: {reason}")
        try:
            agentcore.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=session)
        except Exception as exc:  # noqa: BLE001 - best effort, logged
            log(f"stop failed: {type(exc).__name__}: {exc}")

    def stale_code() -> str:
        """AgentCore hands new sessions to microVMs that may have started before the last push_code, with the code they
        loaded then. The report says which code this run really uses: a run on the wrong code is stopped at once."""
        expected = args.expect_version or json.loads(boto3.client("s3", region_name=region).get_object(
            Bucket=tf_output("code_bucket"), Key="code/current.json")["Body"].read())["version"]
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(5)
            try:
                commit = ((invoke({"action": "report", "run_id": run_id}).get("report") or {}).get("versions") or {}).get("platform", {}).get("commit", "")
            except Exception:  # noqa: BLE001 - the record does not exist yet
                continue
            if commit:
                loaded = commit.rsplit("@", 1)[-1]
                log(f"code in use: {commit} (expected {expected})")
                return "" if loaded == expected else f"the run started on code {loaded}, but {expected} was published"
        log("could not read the code version of the run; continuing")
        return ""

    def forget_run() -> None:
        """Leave nothing behind for a run stopped by hand: its record and its EFS access point."""
        ddb, efs, fs = boto3.client("dynamodb", region_name=region), boto3.client("efs", region_name=region), tf_output("efs_file_system_id")
        try:
            item = ddb.get_item(TableName="magi-runs", Key={"pk": {"S": f"run#{run_id}"}}, ConsistentRead=True).get("Item")
            if item and item["status"]["S"] == "RUNNING":
                rec = {**json.loads(item["record"]["S"]), "resumable": False, "abort_reason": "stopped: stale code"}
                ddb.update_item(TableName="magi-runs", Key={"pk": {"S": f"run#{run_id}"}}, UpdateExpression="SET #s = :a, #r = :rec",
                                ExpressionAttributeNames={"#s": "status", "#r": "record"},
                                ExpressionAttributeValues={":a": {"S": "ABORTED"}, ":rec": {"S": json.dumps(rec, sort_keys=True)}})
            for ap in efs.describe_access_points(FileSystemId=fs)["AccessPoints"]:
                if {t["Key"]: t["Value"] for t in ap.get("Tags", [])}.get("magi-run") == run_id:
                    efs.delete_access_point(AccessPointId=ap["AccessPointId"])
        except Exception as exc:  # noqa: BLE001 - best effort, logged
            log(f"cleanup failed: {type(exc).__name__}: {exc}")

    problem = stale_code()
    if problem:
        log(f"STALE CODE: {problem}. Stopping the run before it spends anything; start it again.")
        stop_session("stale code")
        forget_run()
        attempt = int(os.environ.get("MAGI_STALE_RETRY", "0"))
        if attempt < args.retries:  # old containers are used up one per attempt (they expire after 15 idle minutes)
            log(f"starting again ({attempt + 1}/{args.retries})")
            os.environ["MAGI_STALE_RETRY"] = str(attempt + 1)
            os.execv(sys.executable, [sys.executable, *sys.argv])
        return 3

    status = {}
    while True:
        time.sleep(args.poll)
        try:
            pull_events()
            status = invoke({"action": "status", "run_id": run_id})
        except Exception as exc:  # noqa: BLE001 - transient AWS errors must not end the watch
            log(f"poll error: {type(exc).__name__}: {exc}")
            continue
        st = status.get("status") or status.get("error") or "?"
        if st != last_status:
            log(f"STATUS {st}")
            last_status = st
        if st not in ACTIVE:
            break
        dead_polls = dead_polls + 1 if status.get("active_in_session") is False else 0
        if dead_polls >= 3:
            log("the session no longer drives the run (resume it with the `resume` action)")
            break
        if cost > args.kill_above:
            killed = f"model cost ${cost:.2f} above --kill-above {args.kill_above}"
            stop_session(killed)
            break
        if time.time() - started > args.max_wait:
            killed = f"more than {args.max_wait:.0f} s"
            stop_session(killed)
            break
    time.sleep(5)
    try:
        pull_events()
    except Exception as exc:  # noqa: BLE001
        log(f"final event pull failed: {type(exc).__name__}: {exc}")

    # -- result and summary ---------------------------------------------------------------
    env = {**__import__("os").environ, "MAGI_RUNTIME_ARN": arn}
    fetch = subprocess.run([str(HERE / "fetch-result.sh"), run_id, str(out.parent)], capture_output=True, text=True, env=env)
    log("fetch-result: " + (fetch.stdout.strip().replace("\n", " | ") or fetch.stderr.strip()[:300]))
    report = {}
    try:
        report = json.loads((out / "report.json").read_text())
    except (OSError, ValueError):
        pass
    outcome = report.get("report", {}).get("outcome", {})
    patch = out / "candidate.patch"
    has_patch = patch.is_file() and patch.stat().st_size > 1
    summary = {
        "run_id": run_id, "repo": args.repo, "ref": args.ref, "status": last_status,
        "outcome": outcome.get("status"), "diagnosis": outcome.get("diagnosis"), "final_commit": outcome.get("final_commit"),
        "stopped_by_watcher": killed or None, "duration_seconds": round(time.time() - started),
        "llm_calls": calls, "tokens_in": tok_in, "tokens_out": tok_out,
        "model_cost_usd": round(cost, 4), "provider_reported_cost_usd": round(provider_cost, 4),
        "infra_active_seconds": round(active_s), "infra_cost_usd_estimate": round(active_s / 3600 * args.infra_usd_per_hour, 4),
        "gates": gates, "has_patch": has_patch, "log_dir": str(out),
    }
    summary["total_cost_usd_estimate"] = round(summary["model_cost_usd"] + summary["infra_cost_usd_estimate"], 4)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    lines = [f"{k}: {v}" for k, v in summary.items() if k != "gates"]
    lines += ["gates:"] + [f"  {g}: {v}" for g, v in sorted(gates.items())]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    log("SUMMARY\n" + "\n".join(lines))

    if args.try_candidate:
        if has_patch:
            base = report.get("report", {}).get("target", {}).get("baseline_commit") or args.ref
            cmd = [str(HERE / "try-candidate.sh"), "--repo", args.repo, "--ref", base, "--patch", str(patch),
                   *shlex.split(args.try_candidate_args)]
            log("try-candidate: " + shlex.join(cmd))
            with (out / "try-candidate.log").open("w") as fh:
                rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
            log(f"try-candidate exit code {rc} (see {out / 'try-candidate.log'})")
        else:
            log("try-candidate skipped: the run produced no patch")
    log("done")
    return 0 if last_status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
