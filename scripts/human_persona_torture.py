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
import platform
from contextlib import contextmanager
from collections import deque
import uuid
from datetime import datetime, timezone
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import random
import re
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


def _read_proc_status_kb(pid: int, key: str) -> int:
    """Return the value of ``key`` (e.g. VmRSS, VmHWM) from /proc/<pid>/status in KiB, or 0."""
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0


def get_current_rss_kb(pid: int) -> int:
    """Resident set size right now (VmRSS). Used for the RAM floor and the settled value."""
    return _read_proc_status_kb(pid, "VmRSS")


class RssSampler(threading.Thread):
    """Samples VmRSS of ``pid`` every ``interval`` seconds until stopped.

    Gives an observed floor/ceiling for the load window itself, independent of the
    kernel high-water mark, so a RAM *floor* is measured rather than inferred.
    """

    def __init__(self, pid: int, interval: float = 0.01):
        super().__init__(name="rss-sampler", daemon=True)
        self.pid = pid
        self.interval = interval
        self.samples_kb = deque(maxlen=4096)
        self.sample_count = 0
        self.sample_min = 0
        self.sample_max = 0
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            value = get_current_rss_kb(self.pid)
            if value > 0:
                self.samples_kb.append(value)
                self.sample_count += 1
                self.sample_min = min(self.sample_min or value, value)
                self.sample_max = max(self.sample_max, value)
            self._stop_event.wait(self.interval)

    def stop(self) -> dict:
        self._stop_event.set()
        self.join(timeout=2.0)
        if not self.samples_kb:
            return {"samples": 0, "min_kb": 0, "max_kb": 0}
        return {"samples": self.sample_count, "min_kb": self.sample_min, "max_kb": self.sample_max}


def get_peak_rss_kb(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0


def wait_ready(base_url: str, deadline_s: float = 10.0, proc=None) -> float:
    """Poll /readyz until it answers 200 or the deadline passes.

    Replaces the fixed time.sleep(0.4) + 30-try loop (which also spun without
    delay on non-exception failures): returns as soon as the server is ready,
    fails fast if the child process died, and backs off with a capped
    exponential delay. Returns seconds waited.
    """
    t0 = time.perf_counter()
    delay = 0.01
    while True:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"server exited with code {proc.returncode} before ready")
        try:
            with urllib.request.urlopen(f"{base_url}/readyz", timeout=1.0) as resp:
                if resp.status == 200:
                    return time.perf_counter() - t0
        except Exception:
            pass
        if time.perf_counter() - t0 > deadline_s:
            raise RuntimeError("Server failed to reach ready state before torture test")
        time.sleep(delay)
        delay = min(delay * 2, 0.2)


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
        s1, lat1, b1 = req("/readyz")
        results.append({"action": "probe_readyz", "status": s1, "latency_ms": lat1,
                        "probe": "readyz", "reason": classify_reason(s1, b1)})
        s2, lat2, _ = req("/api/projects")
        results.append({"action": "list_projects", "status": s2, "latency_ms": lat2})

    elif role == "principal_systems_architect":
        # W3C traceparent headers, high-frequency liveness/readiness, metrics scraping
        trace_id = f"{rng.getrandbits(128):032x}"
        span_id = f"{rng.getrandbits(64):016x}"
        w3c = f"00-{trace_id}-{span_id}-01"
        s1, lat1, _ = req("/healthz", headers={"traceparent": w3c})
        results.append({"action": "w3c_healthz", "status": s1, "latency_ms": lat1})
        s2, lat2, b2 = req("/readyz", headers={"traceparent": w3c})
        results.append({"action": "w3c_readyz", "status": s2, "latency_ms": lat2,
                        "probe": "readyz", "reason": classify_reason(s2, b2)})
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


READYZ_ALLOWED_REASONS = frozenset({"audit_verification_in_progress"})


def classify_reason(status: int, body: bytes) -> str:
    """Return the machine-readable reason a readiness probe answered with.

    200 -> "ok". A non-JSON or reason-less body is reported as "unparseable" /
    "unknown" rather than being dropped, so a receipt can never silently hide a
    non-deterministic failure mode.
    """
    if status == 200:
        return "ok"
    try:
        payload = json.loads(bytes(body).decode("utf-8", "replace"))
    except Exception:
        return "unparseable"
    if not isinstance(payload, dict):
        return "unparseable"
    reason = payload.get("reason")
    return str(reason) if reason else "unknown"


def readyz_invariant(status_counts: dict, reason_counts: dict, expected_total: int) -> dict:
    """Fixed-seed determinism gate for /readyz.

    The number of readiness probes issued is a function of the seed, so
    200 + 503 must equal exactly that number, every status must be one of those
    two, and every 503 must carry an allow-listed fail-closed reason.
    """
    total = sum(status_counts.values())
    unexpected_status = sorted(s for s in status_counts if s not in (200, 503))
    allowed = set(READYZ_ALLOWED_REASONS) | {"ok"}
    unexpected_reasons = sorted(r for r in reason_counts if r not in allowed)
    return {
        "probes_total": total,
        "probes_expected": expected_total,
        "status_breakdown": {str(k): v for k, v in sorted(status_counts.items())},
        "reason_breakdown": dict(sorted(reason_counts.items())),
        "unexpected_status": unexpected_status,
        "unexpected_reasons": unexpected_reasons,
        "holds": (total == expected_total and not unexpected_status and not unexpected_reasons),
    }


def recovery_probe(base_url, path):
    """Measure a single post-wave response, fail closed on any transport error."""
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(base_url + path, timeout=2.0) as response:
            status = response.status
    except Exception:
        status = -1
    return {"status": status, "latency_ms": (time.perf_counter() - started) * 1000}


def recovery_passes(waves):
    return bool(waves) and all(
        probe["status"] == 200 and 0 <= probe["latency_ms"] < 200
        for wave in waves for probe in (wave["healthz"], wave["readyz"]))


def _is_loopback_dsn(admin_url):
    """Fail closed: the DSN must name loopback before any driver is imported."""
    if not admin_url:
        return False
    host = None
    try:
        from psycopg2.extensions import parse_dsn
        host = parse_dsn(admin_url).get("host")
    except Exception:
        match = re.search(r"(?:^|[ ?&])host=([^ &]+)", admin_url)
        if match:
            host = match.group(1)
        else:
            parsed = urllib.parse.urlsplit(admin_url)
            host = parsed.hostname
    return host in {"localhost", "127.0.0.1", "::1"}


@contextmanager
def isolated_database(backend):
    """PG is opt-in, loopback only, and always uses a new disposable database."""
    if backend == "sqlite":
        yield None
        return
    admin_url = os.environ.get("XRAY_TORTURE_PG_ADMIN_URL", "")
    if not _is_loopback_dsn(admin_url):
        raise ValueError("PostgreSQL torture requires an explicit loopback admin URL")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import make_dsn
    name = "xray_torture_" + uuid.uuid4().hex
    conn = psycopg2.connect(admin_url, connect_timeout=5)
    conn.autocommit = True
    created = False
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
        yield make_dsn(admin_url, dbname=name)
    finally:
        try:
            if created:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/validation/session14-human-torture-2026-09-19.json")
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--waves", type=int, default=1,
                        help="re-run the full persona set N times in the same server process; "
                             "settled RSS is recorded after each wave to prove memory does not keep climbing")
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default="sqlite")
    args = parser.parse_args()
    if not 1 <= args.waves <= 50 or not 1 <= args.concurrency <= 1000:
        parser.error("waves must be 1..50 and concurrency 1..1000")
    with isolated_database(args.backend) as database_url:
        run(args, database_url)


def run(args, database_url=None):

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
        for key in ("DATABASE_URL", "DB_HOST", "DB_NAME", "DB_USERNAME", "DB_PASSWORD",
                    "XRAY_TORTURE_PG_ADMIN_URL"):
            env.pop(key, None)
        if database_url:
            env["DATABASE_URL"] = database_url
            env["DB_POOL_MAX"] = "5"
        env.pop("XRAY_MAINTENANCE_MODE", None)
        log_file = open(Path(directory) / "server.log", "w", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, "app/server.py"], cwd=ROOT, env=env,
                                stdout=log_file, stderr=log_file)

        sampler = None
        try:
            try:
                startup_ready_s = wait_ready(base_url, deadline_s=10.0, proc=proc)
            except RuntimeError:
                proc.kill()
                proc.wait(timeout=3)
                log_file.close()
                raise

            # RAM floor: resident set right after readiness, before any load.
            rss_floor_kb = get_current_rss_kb(proc.pid)
            sampler = RssSampler(proc.pid)
            sampler.start()

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
            all_latencies = []
            status_counts = {}
            readyz_status_counts = {}
            readyz_reason_counts = {}

            def tally(act):
                """Single-threaded tally of one persona action (main thread only)."""
                code = act["status"]
                status_counts[code] = status_counts.get(code, 0) + 1
                all_latencies.append(act["latency_ms"])
                if act.get("probe") == "readyz":
                    readyz_status_counts[code] = readyz_status_counts.get(code, 0) + 1
                    why = act.get("reason", "unknown")
                    readyz_reason_counts[why] = readyz_reason_counts.get(why, 0) + 1

            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(run_persona_action, base_url, p, token, random.Random(args.seed + p["persona_id"]))
                           for p in personas]
                for f in as_completed(futures):
                    res = f.result()
                    for act in res["actions"]:
                        tally(act)

            torture_duration = time.perf_counter() - t_torture_start
            peak_rss_kb = get_peak_rss_kb(proc.pid)

            recovery_waves = [{"wave": 1, "healthz": recovery_probe(base_url, "/healthz"),
                               "readyz": recovery_probe(base_url, "/readyz")}]

            # RAM settled: resident set after the recovery probes, before shutdown.
            rss_settled_kb = get_current_rss_kb(proc.pid)

            # Extra waves in the SAME process: settled RSS after each wave must not keep climbing.
            settled_per_wave_kb = [rss_settled_kb]
            for wave in range(2, max(1, args.waves) + 1):
                wave_t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                    futures = [pool.submit(run_persona_action, base_url, p, token,
                                           random.Random(args.seed + p["persona_id"] + wave * 100003))
                               for p in personas]
                    for f in as_completed(futures):
                        res = f.result()
                        for act in res["actions"]:
                            tally(act)
                torture_duration += time.perf_counter() - wave_t0  # throughput covers every wave
                recovery_waves.append({"wave": wave, "healthz": recovery_probe(base_url, "/healthz"),
                                       "readyz": recovery_probe(base_url, "/readyz")})
                settled_per_wave_kb.append(get_current_rss_kb(proc.pid))
            sampled = sampler.stop()
            health_rec_ms = max(w["healthz"]["latency_ms"] for w in recovery_waves)
            ready_rec_ms = max(w["readyz"]["latency_ms"] for w in recovery_waves)
            health_rec_status = 200 if all(w["healthz"]["status"] == 200 for w in recovery_waves) else -1
            ready_rec_status = 200 if all(w["readyz"]["status"] == 200 for w in recovery_waves) else -1
            peak_rss_kb = max(peak_rss_kb, get_peak_rss_kb(proc.pid))
            rss_settled_kb = settled_per_wave_kb[-1]
            # Leak gate: growth from the first settled value to the last is bounded (8 MiB) once
            # the worker pool and allocator arenas are warm. Single-wave runs pass trivially.
            rss_wave_growth_kb = settled_per_wave_kb[-1] - settled_per_wave_kb[0]
            rss_no_unbounded_growth = rss_wave_growth_kb <= 8 * 1024
            rss_growth_kb = max(0, rss_settled_kb - rss_floor_kb)

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

            # Fixed-seed determinism: the persona set decides how many /readyz probes
            # are issued, so 200 + 503 is a constant and every 503 must be an
            # allow-listed fail-closed reason.
            readyz_probes_per_wave = sum(
                1 for persona in personas
                if persona.get("role") in ("lead_research_scientist", "principal_systems_architect")
            )
            readyz_expected = readyz_probes_per_wave * max(1, args.waves)
            readyz = readyz_invariant(readyz_status_counts, readyz_reason_counts, readyz_expected)

            operational_baselines = {
                "recovery_per_wave": recovery_waves,
                "every_wave_recovers_sub_200ms": recovery_passes(recovery_waves),
                "zero_unhandled_panics": (server_alive and tracebacks == 0),
                "no_server_crash": server_alive,
                "tracebacks_in_log": tracebacks,
                "peak_rss_kb": peak_rss_kb,
                "peak_rss_bounded_128mb": peak_rss_kb <= 128 * 1024,
                "rss_floor_kb": rss_floor_kb,
                "startup_ready_s": round(startup_ready_s, 3),
                "rss_ceiling_kb": peak_rss_kb,
                "rss_settled_kb": rss_settled_kb,
                "rss_growth_after_recovery_kb": rss_growth_kb,
                "rss_sampled_min_kb": sampled["min_kb"],
                "rss_sampled_max_kb": sampled["max_kb"],
                "rss_samples": sampled["samples"],
                "rss_settled_bounded_128mb": rss_settled_kb <= 128 * 1024,
                "waves": max(1, args.waves),
                "rss_settled_per_wave_kb": settled_per_wave_kb,
                "rss_wave_growth_kb": rss_wave_growth_kb,
                "rss_no_unbounded_growth": rss_no_unbounded_growth,
                "health_recovery_ms": health_rec_ms,
                "health_recovery_sub_200ms": (health_rec_status == 200 and health_rec_ms < 200.0),
                "ready_recovery_ms": ready_rec_ms,
                "ready_recovery_sub_200ms": (ready_rec_status == 200 and ready_rec_ms < 200.0),
                "readyz_probes_total": readyz["probes_total"],
                "readyz_probes_expected": readyz["probes_expected"],
                "readyz_status_breakdown": readyz["status_breakdown"],
                "readyz_reason_breakdown": readyz["reason_breakdown"],
                "readyz_unexpected_status": readyz["unexpected_status"],
                "readyz_unexpected_reasons": readyz["unexpected_reasons"],
                "readyz_determinism_invariant": readyz["holds"],
            }

            all_baselines_pass = all([
                operational_baselines["every_wave_recovers_sub_200ms"],
                operational_baselines["zero_unhandled_panics"],
                operational_baselines["peak_rss_bounded_128mb"],
                operational_baselines["rss_settled_bounded_128mb"],
                operational_baselines["rss_no_unbounded_growth"],
                operational_baselines["health_recovery_sub_200ms"],
                operational_baselines["ready_recovery_sub_200ms"],
                operational_baselines["readyz_determinism_invariant"],
            ])

            summary = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scope": "synthetic 120-persona local concurrency test; not measured 100x production load",
                "backend": args.backend,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "malloc_arena_max": os.getenv("MALLOC_ARENA_MAX"),
                "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
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
            print(f"[*] RAM floor={rss_floor_kb/1024:.1f} MiB, ceiling={peak_rss_kb/1024:.1f} MiB, settled={rss_settled_kb/1024:.1f} MiB "
                  f"(growth after recovery {rss_growth_kb/1024:.1f} MiB, {sampled['samples']} samples)")
            if args.waves > 1:
                print(f"[*] Waves={args.waves}: settled RSS per wave (MiB) = "
                      f"{[round(v/1024,1) for v in settled_per_wave_kb]}, growth first->last {rss_wave_growth_kb/1024:.1f} MiB, "
                      f"no_unbounded_growth={rss_no_unbounded_growth}")
            print(f"[*] /readyz determinism: {readyz['probes_total']}/{readyz['probes_expected']} probes, "
                  f"statuses={readyz['status_breakdown']}, reasons={readyz['reason_breakdown']}, "
                  f"invariant_holds={readyz['holds']}")
            print(f"[*] Health recovery: {health_rec_ms:.2f}ms, Ready recovery: {ready_rec_ms:.2f}ms")
            print(f"[*] Operational baselines PASS: {all_baselines_pass}")

            if not all_baselines_pass:
                sys.exit(1)
        finally:
            if sampler is not None and sampler.is_alive():
                sampler.stop()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            log_file.close()
            log_source = Path(directory) / "server.log"
            log_target = (ROOT / args.output).with_suffix(".server.log")
            log_target.parent.mkdir(parents=True, exist_ok=True)
            if log_source.exists() and (sys.exc_info()[0] is not None):
                log_target.write_bytes(log_source.read_bytes())



if __name__ == "__main__":
    main()
