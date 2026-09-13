#!/usr/bin/env python3
"""Deterministic HTTP_EXEC_PARALLELISM sweep (research-grade receipt).

Runs scripts/stress_local.py once per candidate execution-parallelism value in a
fixed, deterministic order and emits a single JSON receipt with the 100-client
read/write percentiles for each value. This turns the choice of the
HTTP_EXEC_PARALLELISM default from an assertion into a reproducible measurement.

LOCAL ONLY: it drives the in-repo synthetic workload, never an external target.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
STRESS = ROOT / "scripts" / "stress_local.py"
PHASES = ("100_client_reads", "100_client_writes", "100_client_idempotency_race")


def run_one(value: int, requests: int | None) -> dict:
    env = dict(os.environ)
    env["HTTP_EXEC_PARALLELISM"] = str(value)
    env["PYTHONHASHSEED"] = "0"
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "stress.json"
        cmd = [sys.executable, str(STRESS), "--output", str(out)]
        if requests:
            cmd += ["--requests", str(requests)]
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            return {"exec_parallelism": value, "error": proc.stderr.strip()[-500:]}
        data = json.loads(out.read_text())
    phases = {p["name"]: p for p in data.get("phases", [])}
    summary = {"exec_parallelism": value, "safety_pass": data.get("safety_pass")}
    for name in PHASES:
        p = phases.get(name)
        if not p:
            continue
        summary[name] = {
            "rps": p["requests_per_second"],
            "p50_ms": p["p50_ms"],
            "p95_ms": p["p95_ms"],
            "p99_ms": p["p99_ms"],
            "statuses": p["statuses"],
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic execution-parallelism sweep")
    ap.add_argument("--values", default="2,3,4,6,8")
    ap.add_argument("--requests", type=int, default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    values = [int(v) for v in args.values.split(",") if v.strip()]
    if any(v <= 0 for v in values):
        raise SystemExit("--values must be positive integers")

    results = [run_one(v, args.requests) for v in sorted(values)]
    ok = [r for r in results if "error" not in r and r.get("100_client_reads")]
    best = min(ok, key=lambda r: r["100_client_reads"]["p95_ms"]) if ok else None
    receipt = {
        "tool": "bench_sweep",
        "scope": "local synthetic SQLite workload; comparative, not capacity",
        "cpu_count": os.cpu_count(),
        "values": values,
        "results": results,
        "best_read_p95": best and {
            "exec_parallelism": best["exec_parallelism"],
            "p95_ms": best["100_client_reads"]["p95_ms"],
        },
        "all_pass": bool(ok) and len(ok) == len(results) and all(r.get("safety_pass") for r in ok),
    }
    text = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output:
        pathlib.Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if receipt["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
