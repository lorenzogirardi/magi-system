#!/usr/bin/env python3
"""Collect the files of an AgentCore run into examples/<run_id>-agentcore/, like the case-study folders.

    uv run python deploy/agentcore/collect_example.py magi-results/<run_id> --request request.md

Reads what deploy/agentcore/run_and_log.py wrote and creates:
  request.md                   the change request given to the run
  artifacts/events.jsonl       every event of the run
  artifacts/report.json        the run report (what the runtime returns for `report`)
  artifacts/report.md          the same report rendered as Markdown (magi_system.service.app.render_report_md)
  artifacts/candidate.patch    the candidate patch (empty when the run produced none)
  artifacts/run.log, status.json, summary.json, summary.txt, runtime-errors.log, try-candidate.log
  artifacts/stats.json         numbers derived from the events: effort per agent, node durations,
                               gate verdicts, cost (model and estimated infrastructure), timeline
article.md and training.md are written by hand from these numbers.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import shutil
from pathlib import Path

from magi_system.service.app import render_report_md

COPY = ["events.jsonl", "candidate.patch", "run.log", "status.json", "summary.json", "summary.txt",
        "runtime-errors.log", "try-candidate.log"]


def stats(events: list[dict], infra_usd_per_hour: float) -> dict:
    heartbeat = sum(e.get("interval_s") or 0 for e in events if e["kind"] == "heartbeat")  # heartbeats carry no `ts`
    events = sorted((e for e in events if "ts" in e), key=lambda e: e["ts"])
    clock = lambda e: dt.datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S")  # noqa: E731
    per_agent: dict[str, dict] = collections.defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 0})
    per_purpose: collections.Counter = collections.Counter()
    for e in events:
        if e["kind"] == "llm_call":
            a = per_agent[e.get("agent", "?")]
            a["calls"] += 1
            a["input_tokens"] += e.get("input_tokens") or 0
            a["output_tokens"] += e.get("output_tokens") or 0
            a["cost_usd"] += e.get("cost_usd") or 0
            a["latency_ms"] += e.get("latency_ms") or 0
            per_purpose[e.get("purpose", "?")] += 1
    for a in per_agent.values():
        a["cost_usd"] = round(a["cost_usd"], 4)
        a["avg_latency_ms"] = round(a["latency_ms"] / a["calls"]) if a["calls"] else 0
        del a["latency_ms"]
    starts = [e for e in events if e["kind"] == "node_start"]
    end_ts = next((e["ts"] for e in reversed(events) if e["kind"] == "run_end"), events[-1]["ts"] if events else 0)
    nodes = []
    for a, b in zip(starts, starts[1:] + [{"ts": end_ts}], strict=True):
        nodes.append({"node": a.get("node"), "round": a.get("round"), "start": clock(a), "seconds": round(b["ts"] - a["ts"], 1)})
    gates: dict[str, dict] = {}
    for e in events:
        if e["kind"] == "check":
            gates[e.get("gate", "?")] = {"verdict": e.get("verdict"), "exit_code": e.get("exit_code"), "command": e.get("command")}
    tools = collections.Counter((e.get("agent"), e.get("tool")) for e in events if e["kind"] == "tool_call")
    first, last = (events[0]["ts"], end_ts) if events else (0, 0)
    return {
        "events": len(events), "kinds": dict(collections.Counter(e["kind"] for e in events).most_common()),
        "started": clock(events[0]) if events else None, "ended": dt.datetime.fromtimestamp(last).strftime("%H:%M:%S") if events else None,
        "duration_seconds": round(last - first), "per_agent": dict(sorted(per_agent.items())),
        "llm_calls_by_purpose": dict(per_purpose.most_common()),
        "totals": {k: round(sum(a[k] for a in per_agent.values()), 4) for k in ("calls", "input_tokens", "output_tokens", "cost_usd")},
        "nodes": nodes, "slowest_nodes": sorted(nodes, key=lambda n: -n["seconds"])[:8], "gates": gates,
        "tool_calls": {f"{a}:{t}": n for (a, t), n in tools.most_common(15)},
        "infra": {"active_seconds": round(heartbeat), "usd_per_hour": infra_usd_per_hour,
                  "cost_usd_estimate": round(heartbeat / 3600 * infra_usd_per_hour, 4)},
        "failures": [{"at": clock(e), "kind": e["kind"], "detail": (e.get("failure") or e.get("detail") or "")[:200]}
                     for e in events if e["kind"] in ("agent_failure", "infra_error", "technical_retry", "budget_exhausted", "policy_violation")],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="magi-results/<run_id>")
    ap.add_argument("--request", required=True, help="the request.md given to the run")
    ap.add_argument("--examples-dir", default="examples")
    ap.add_argument("--infra-usd-per-hour", type=float, default=0.27)
    args = ap.parse_args()

    src = Path(args.run_dir)
    run_id = src.name
    dest = Path(args.examples_dir) / f"{run_id}-agentcore"
    art = dest / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.request, dest / "request.md")
    for name in COPY:
        if (src / name).is_file():
            shutil.copy(src / name, art / name)
    events = [json.loads(line) for line in (src / "events.jsonl").read_text().splitlines() if line.strip()]
    (art / "stats.json").write_text(json.dumps(stats(events, args.infra_usd_per_hour), indent=2))
    if (src / "report.json").is_file():
        full = json.loads((src / "report.json").read_text())
        rep = full.get("report", full)
        (art / "report.json").write_text(json.dumps(full, indent=2, sort_keys=True))
        (art / "report.md").write_text(render_report_md(rep))
    print(f"collected into {dest}")
    for p in sorted(dest.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(dest)}  {p.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
