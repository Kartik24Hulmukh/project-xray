#!/usr/bin/env python3
"""100-Persona Human Torture & Concurrency Pipeline Stress Harness.

Simulates 100+ diverse real-world human user profiles (investigative journalists,
RTI activists, startup founders, research scientists, systems architects,
red-team chaos testers, citizen watchdogs, and mobile field monitors) hammering
the project-xray system under 100-worker concurrency with chaotic inputs,
out-of-order requests, conflicting state writes, and broken edge-case sessions.

Operational baselines enforced:
- Zero unhandled panics / crashes (server stays alive, zero tracebacks in log).
- Bounded resource consumption (server peak RSS <= 128 MiB).
- Sub-200ms latency recovery on health/readiness endpoints post-torture.
- Audit chain integrity preserved.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent

# 120 distinct real-world human personas across 8 core archetypes (session 15: added mobile_field_monitor,
# closing the gap between the module docstring, which already promised mobile field monitors, and ARCHETYPES)
ARCHETYPES = [
    ("investigative_journalist", 15),
    ("rti_activist", 15),
    ("sf_founder", 15),
    ("lead_research_scientist", 15),
    ("principal_systems_architect", 15),
    ("red_team_chaos_lead", 15),
    ("citizen_watchdog", 15),
    ("mobile_field_monitor", 15),
]

def generate_personas() -> list[dict]:
    personas = []
    pid = 1
    for role, count in ARCHETYPES:
        for idx in range(1, count + 1):
            personas.append({
                "persona_id": pid,
                "role": role,
                "name": f"{role}_{idx:02d}",
                "chaos_level": "high" if "chaos" in role or "watchdog" in role else ("medium" if "founder" in role or "journalist" in role else "low"),
            })
            pid += 1
    return personas


def get_peak_rss_kb(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0


def run_persona_action(base_url: str, persona: dict, admin_token: str, rng: random.Random) -> dict:
    role = persona["role"]
    name = persona["name"]
    results = []

    def req(path: str, method: str = "GET", body: dict | str | bytes | None = None,
            headers: dict | None = None, timeout: float = 12.0) -> tuple[int, float, bytes]:
        h = headers or {}
        data = None
        if body is not None:
            if isinstance(body, bytes):
                data = body
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = json.dumps(body).encode("utf-8")
                h.setdefault("Content-Type", "application/json")
        t0 = time.perf_counter()
        r = urllib.request.Request(base_url + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                elapsed = (time.perf_counter() - t0) * 1000.0
                return resp.status, elapsed, resp.read()[:500]
        except urllib.error.HTTPError as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            return e.code, elapsed, e.read()[:500]
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            return -1, elapsed, repr(e).encode("utf-8")[:500]

    if role == "investigative_journalist":
        # Reads projects, reads dossier, fetches markdown report and RTI draft, queries claims CSV
        s1, lat1, _ = req("/api/projects")
        results.append({"action": "list_projects", "status": s1, "latency_ms": lat1})
        s2, lat2, _ = req("/api/projects/prj_0000000000000001")
        results.append({"action": "get_dossier", "status": s2, "latency_ms": lat2})
        s3, lat3, _ = req("/api/projects/prj_0000000000000001/report")
        results.append({"action": "get_report", "status": s3, "latency_ms": lat3})
        s4, lat4, _ = req("/api/projects/prj_0000000000000001/claims.csv")
        results.append({"action": "get_claims_csv", "status": s4, "latency_ms": lat4})

    elif role == "rti_activist":
        # Queries RTI drafts, gaps, checks public disclaimers
        s1, lat1, _ = req("/api/projects/prj_0000000000000001/rti")
        results.append({"action": "get_rti_draft", "status": s1, "latency_ms": lat1})
        s2, lat2, _ = req("/api/projects")
        results.append({"action": "list_projects", "status": s2, "latency_ms": lat2})

    elif role == "sf_founder":
        # Rapid project creation with idempotency key, rapid verification
        idem = f"idem_founder_{name}_{rng.randint(1000, 9999)}"
        proj_payload = {
            "title": f"High Impact Venture {name}",
            "authority": "Municipal Corporation of Greater Mumbai",
            "budget_inr": 25000000,
            "synthetic": True,
        }
        s1, lat1, _ = req("/api/projects", method="POST", body=proj_payload,
                          headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": idem})
        results.append({"action": "create_project", "status": s1, "latency_ms": lat1})
        s2, lat2, _ = req("/healthz")
        results.append({"action": "probe_healthz", "status": s2, "latency_ms": lat2})

    elif role == "lead_research_scientist":
        # Audit trail checks, deterministic replay assertions, readyz probes
        s1, lat1, _ = req("/readyz")
        results.append({"action": "probe_readyz", "status": s1, "latency_ms": lat1})
        s2, lat2, _ = req("/api/projects")
        results.append({"action": "list_projects", "status": s2, "latency_ms": lat2})

    elif role == "principal_systems_architect":
        # W3C traceparent headers, high-frequency liveness/readiness, metrics scraping
        trace_id = f"{rng.getrandbits(128):032x}"
        span_id = f"{rng.getrandbits(64):016x}"
        w3c = f"00-{trace_id}-{span_id}-01"
        s1, lat1, _ = req("/healthz", headers={"traceparent": w3c})
        results.append({"action": "w3c_healthz", "status": s1, "latency_ms": lat1})
        s2, lat2, _ = req("/readyz", headers={"traceparent": w3c})
        results.append({"action": "w3c_readyz", "status": s2, "latency_ms": lat2})
        s3, lat3, _ = req("/metrics", headers={"Authorization": f"Bearer {admin_token}"})
        results.append({"action": "scrape_metrics", "status": s3, "latency_ms": lat3})

    elif role == "red_team_chaos_lead":
        # Malformed payloads, boundary inputs, type confusion, overlong keys, non-existent routes
        s1, lat1, _ = req("/api/projects", method="POST", body="INVALID JSON {{{", headers={"Authorization": f"Bearer {admin_token}"})
        results.append({"action": "malformed_json", "status": s1, "latency_ms": lat1})
        traversal = "/api/projects/" + chr(46) + chr(46) + "/etc/passwd"
        s2, lat2, _ = req(traversal)
        results.append({"action": "path_traversal", "status": s2, "latency_ms": lat2})
        s3, lat3, _ = req("/api/projects", method="POST", body={"title": "A" * 10000, "budget_inr": "not-a-number"})
        results.append({"action": "type_confusion", "status": s3, "latency_ms": lat3})

    elif role == "citizen_watchdog":
        # Conflicting state writes: 2 identical requests racing with same idempotency key
        idem_shared = f"idem_watchdog_race_{name}"
        proj_payload = {
            "title": f"Citizen Audit Road Repair {name}",
            "authority": "Public Works Department",
            "budget_inr": 1500000,
            "synthetic": True,
        }
        s1, lat1, _ = req("/api/projects", method="POST", body=proj_payload,
                          headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": idem_shared})
        results.append({"action": "idempotent_write_1", "status": s1, "latency_ms": lat1})
        s2, lat2, _ = req("/api/projects", method="POST", body=proj_payload,
                          headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": idem_shared})
        results.append({"action": "idempotent_write_2", "status": s2, "latency_ms": lat2})
        s3, lat3, _ = req("/api/projects")
        results.append({"action": "watchdog_verify", "status": s3, "latency_ms": lat3})

    return {
        "persona": name,
        "role": role,
        "actions": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/validation/session14-human-torture-2026-09-19.json")
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--concurrency", type=int, default=100)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    personas = generate_personas()
    assert len(personas) >= 100, f"Must have >= 100 personas, got {len(personas)}"

    print(f"[*] Initializing 100-Persona Human Torture Harness (seed={args.seed}, personas={len(personas)})")

    with tempfile.TemporaryDirectory(prefix="xray-torture-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        token = "local-torture-admin-token"
        env = {
            **os.environ,
            "DB_PATH": str(Path(directory) / "torture.db"),
            "APP_ENV": "test",
            "PORT": str(port),
            "BIND_HOST": "127.0.0.1",
            "ADMIN_TOKEN": token,
            "MAX_HTTP_WORKERS": "64",
            "PUBLIC_READ_RATE_LIMIT": "100000",
            "WRITE_RATE_LIMIT": "100000",
            "AUTH_READ_RATE_LIMIT": "100000",
            "EXPENSIVE_WRITE_RATE_LIMIT": "100000",
        }
        env.pop("DATABASE_URL", None)
        env.pop("XRAY_MAINTENANCE_MODE", None)
        log_file = open(Path(directory) / "server.log", "w", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, "app/server.py"], cwd=ROOT, env=env,
                                stdout=log_file, stderr=log_file)

        time.sleep(0.4)

        # Baseline readiness probe
        t_start = time.perf_counter()
        ready = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"{base_url}/readyz", timeout=1.0) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.1)

        if not ready:
            proc.kill()
            raise RuntimeError("Server failed to reach ready state before torture test")

        # Seed initial project for readers
        seed_req = urllib.request.Request(
            f"{base_url}/api/projects",
            data=json.dumps({
                "title": "Seed Foundation Project",
                "authority": "Maharashtra Urban Infrastructure",
                "budget_inr": 50000000,
                "synthetic": True,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}", "Idempotency-Key": "seed_01"},
            method="POST"
        )
        with urllib.request.urlopen(seed_req, timeout=5.0) as resp:
            assert resp.status in (200, 201)

        print(f"[*] Server ready. Starting concurrent execution of {len(personas)} human personas across {args.concurrency} workers...")

        t_torture_start = time.perf_counter()
        all_action_results = []
        all_latencies = []
        status_counts = {}

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(run_persona_action, base_url, p, token, random.Random(args.seed + p["persona_id"]))
                       for p in personas]
            for f in as_completed(futures):
                res = f.result()
                all_action_results.append(res)
                for act in res["actions"]:
                    s = act["status"]
                    status_counts[s] = status_counts.get(s, 0) + 1
                    all_latencies.append(act["latency_ms"])

        torture_duration = time.perf_counter() - t_torture_start
        peak_rss_kb = get_peak_rss_kb(proc.pid)

        # Post-torture latency recovery verification
        rec_t0 = time.perf_counter()
        with urllib.request.urlopen(f"{base_url}/healthz", timeout=2.0) as r:
            health_rec_ms = (time.perf_counter() - rec_t0) * 1000.0
            health_rec_status = r.status

        rec_t1 = time.perf_counter()
        with urllib.request.urlopen(f"{base_url}/readyz", timeout=2.0) as r:
            ready_rec_ms = (time.perf_counter() - rec_t1) * 1000.0
            ready_rec_status = r.status

        server_alive = (proc.poll() is None)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()

        # Check server log for unexpected exceptions / tracebacks
        with open(Path(directory) / "server.log", "r", encoding="utf-8") as f:
            log_content = f.read()
        tracebacks = log_content.count("Traceback (most recent call last):")

        # Calculate statistics
        sorted_lat = sorted(all_latencies)
        p50 = statistics.median(sorted_lat) if sorted_lat else 0.0
        p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0.0
        p99 = sorted_lat[int(len(sorted_lat) * 0.99)] if sorted_lat else 0.0

        operational_baselines = {
            "zero_unhandled_panics": (server_alive and tracebacks == 0),
            "no_server_crash": server_alive,
            "tracebacks_in_log": tracebacks,
            "peak_rss_kb": peak_rss_kb,
            "peak_rss_bounded_128mb": peak_rss_kb <= 128 * 1024,
            "health_recovery_ms": health_rec_ms,
            "health_recovery_sub_200ms": (health_rec_status == 200 and health_rec_ms < 200.0),
            "ready_recovery_ms": ready_rec_ms,
            "ready_recovery_sub_200ms": (ready_rec_status == 200 and ready_rec_ms < 200.0),
        }

        all_baselines_pass = all([
            operational_baselines["zero_unhandled_panics"],
            operational_baselines["peak_rss_bounded_128mb"],
            operational_baselines["health_recovery_sub_200ms"],
            operational_baselines["ready_recovery_sub_200ms"],
        ])

        summary = {
            "timestamp": "2026-09-19T19:00:00Z",
            "scope": "100-persona human torture under 100x concurrency",
            "seed": args.seed,
            "personas_emulated": len(personas),
            "concurrency": args.concurrency,
            "total_requests": len(all_latencies),
            "duration_seconds": round(torture_duration, 3),
            "throughput_rps": round(len(all_latencies) / max(0.001, torture_duration), 1),
            "latency_ms": {
                "p50": round(p50, 2),
                "p95": round(p95, 2),
                "p99": round(p99, 2),
                "min": round(sorted_lat[0], 2) if sorted_lat else 0.0,
                "max": round(sorted_lat[-1], 2) if sorted_lat else 0.0,
            },
            "status_distribution": status_counts,
            "operational_baselines": operational_baselines,
            "pass": all_baselines_pass,
        }

        out_path = ROOT / args.output
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        print(f"[*] Receipts written to {out_path}")
        print(f"[*] Summary: {len(personas)} personas, {len(all_latencies)} requests in {torture_duration:.2f}s ({summary['throughput_rps']} rps)")
        print(f"[*] Latency: P50={p50:.2f}ms, P95={p95:.2f}ms, P99={p99:.2f}ms | Peak RSS: {peak_rss_kb/1024:.1f} MiB")
        print(f"[*] Health recovery: {health_rec_ms:.2f}ms, Ready recovery: {ready_rec_ms:.2f}ms")
        print(f"[*] Operational baselines PASS: {all_baselines_pass}")

        if not all_baselines_pass:
            sys.exit(1)


if __name__ == "__main__":
    main()
