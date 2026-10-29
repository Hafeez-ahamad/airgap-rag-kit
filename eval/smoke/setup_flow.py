"""REAL setup flow through the kit's API (what the wizard does): scan -> prerequisites ->
dry-run plan -> install (real download) -> real inference check. Records timings and outcome.

Usage (from backend/, server running):
  uv run python ../eval/smoke/setup_flow.py --user ADMIN --password PW [--model ID]
Writes eval/results/setup-flow-<timestamp>-<host>.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--model", help="catalogue id; default: the planner's recommendation")
    ap.add_argument("--timeout", type=float, default=3600)
    args = ap.parse_args()
    c = httpx.Client(base_url=f"{args.base}/api", timeout=600)
    r = c.post("/auth/login", json={"username": args.user, "password": args.password})
    r.raise_for_status()
    # httpx does not send Secure cookies over plain http to 127.0.0.1; browsers do.
    h = {"X-CSRF-Token": r.json()["csrf_token"],
         "Cookie": f"airgap_session={r.cookies['airgap_session']}"}
    out: dict[str, object] = {"level": "L5/L6 real setup flow", "at": datetime.now(UTC).isoformat(),
                              "host": platform.node()}

    t0 = time.monotonic()
    a = c.post("/setup/scan", json={"consent": True}, headers=h).json()
    out["scan_seconds"] = round(time.monotonic() - t0, 1)
    model = args.model or a["recommended_model_id"]
    out.update(tier=a["tier"], recommended=a["recommended_model_id"], model=model,
               ram_available_gib=round((a["report"]["ram_available_bytes"] or 0) / 2**30, 2))
    checks = c.get("/setup/prerequisites", headers=h).json()
    out["prerequisites"] = {x["id"]: x["status"] for x in checks}
    plan = c.post("/setup/plan", json={"model_ids": [model]}, headers=h)
    out["plan_status"] = plan.status_code
    if plan.status_code != 200:
        out["plan_error"] = plan.json()
        return _finish(out, False)
    p = plan.json()
    out.update(plan_ready=p["ready"], plan_download_bytes=p["total_download_bytes"],
               plan_steps=[s["title"] for s in p["steps"]])
    if not p["ready"]:
        return _finish(out, False)

    t0 = time.monotonic()
    started = c.post("/setup/install", json={"model_ids": [model]}, headers=h)
    started.raise_for_status()
    last = ""
    state: dict[str, object] = {}
    while time.monotonic() - t0 < args.timeout:
        state = c.get("/setup/state", headers=h).json()
        job = state.get("job") or {}
        line = " | ".join(
            f"{k}:{v.get('status')}"
            + (f" {100 * (v.get('completed') or 0) // v['total']}%" if v.get("total") else "")
            for k, v in (job.get("items") or {}).items())
        if line != last:
            print(f"[{time.monotonic() - t0:6.0f}s] {job.get('status')} {line}", flush=True)
            last = line
        if job.get("status") in {"verified", "failed", "cancelled"}:
            break
        time.sleep(3)
    job = state.get("job") or {}
    out.update(install_seconds=round(time.monotonic() - t0, 1), job_status=job.get("status"),
               job_error=job.get("error"), job_items=job.get("items"),
               setup_complete=state.get("complete"), active_model_id=state.get("active_model_id"),
               steps={k: v["status"] for k, v in (state.get("steps") or {}).items()})
    return _finish(out, bool(state.get("complete")))


def _finish(out: dict[str, object], ok: bool) -> int:
    out["passed"] = ok
    path = ROOT / "eval" / "results" / f"setup-flow-{datetime.now(UTC):%Y%m%dT%H%M%S}-{platform.node()}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    print(f"\n{'PASS' if ok else 'FAIL'} (raw: {path.relative_to(ROOT)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
