"""No-egress test for the NATIVE profile (spec F21). Two parts:

1. guard:   inside this process, with the app's offline guard on, outbound connects to public
            addresses must raise EgressBlocked. (Proves the in-process guard only.)
2. observe: sample the OS TCP connection table for the app and Ollama processes (by PID)
            for --seconds while you use the app (or with --chat, while this script sends a real
            chat through the API). FAIL if any of them has a connection to a non-private address.

Limitations (stated in every result): sampling can miss very short connections, DNS (UDP)
queries are not observed, and other processes on the host are not checked. A firewall-level
capture on the host is the stronger test and is still required before claiming "offline".
The compose profile has its own test: deploy/security/no_egress_compose.sh.

Usage (from backend/):
  uv run python ../eval/security/no_egress.py --pids 1234,5678 --seconds 60 --chat
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

LIMITATIONS = [
    "connection-table sampling can miss connections shorter than the sampling interval",
    "DNS (UDP) lookups are not observed",
    "only the listed processes are checked, not the whole host",
    "not a substitute for a host firewall capture",
]


def parse_connections(text: str, system: str) -> list[tuple[str, str, int]]:
    """(remote_ip, state, pid) for TCP rows of `netstat -ano` (Windows) or `ss -tnpH` (Linux)."""
    rows: list[tuple[str, str, int]] = []
    for line in text.splitlines():
        parts = line.split()
        if system == "Windows":
            if len(parts) >= 5 and parts[0] == "TCP":
                remote, state, pid = parts[2], parts[3], parts[4]
                host = remote.rsplit(":", 1)[0].strip("[]")
                if pid.isdigit():
                    rows.append((host, state, int(pid)))
        else:
            if len(parts) >= 5:
                state, remote = parts[0], parts[4]
                host = remote.rsplit(":", 1)[0].strip("[]")
                m = re.search(r"pid=(\d+)", line)
                if m:
                    rows.append((host, state, int(m.group(1))))
    return rows


def is_external(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if ip.is_unspecified:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local)


def snapshot() -> list[tuple[str, str, int]]:
    system = platform.system()
    args = ["netstat", "-ano", "-p", "TCP"] if system == "Windows" else ["ss", "-tnpH"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
    return parse_connections(out.stdout, system)


def guard_check() -> dict[str, object]:
    from airgap.offline import EgressBlocked, enforce_offline

    enforce_offline()
    results = {}
    for target in (("1.1.1.1", 443), ("8.8.8.8", 53), ("2606:4700:4700::1111", 443)):
        fam = socket.AF_INET6 if ":" in target[0] else socket.AF_INET
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect(target)
            results[target[0]] = "CONNECTED (leak)"
        except EgressBlocked:
            results[target[0]] = "blocked by guard"
        except OSError as exc:
            results[target[0]] = f"failed before guard? {type(exc).__name__}"
        finally:
            s.close()
    ok = all(v == "blocked by guard" for v in results.values())
    return {"ok": ok, "attempts": results}


def chat_once(base: str, user: str | None, password: str | None) -> str:
    import httpx

    c = httpx.Client(base_url=base, timeout=600)
    h: dict[str, str] = {}
    if user and password:
        r = c.post("/api/auth/login", json={"username": user, "password": password})
        r.raise_for_status()
        h["X-CSRF-Token"] = r.json()["csrf_token"]
        # httpx does not send Secure cookies over http://127.0.0.1 (browsers do): pass it on.
        h["Cookie"] = f"airgap_session={r.cookies['airgap_session']}"
    r = c.post("/api/chat", json={"question": "Reply with one word: ok", "mode": "assistant"},
               headers=h)
    return f"HTTP {r.status_code}, {len(r.text)} bytes"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pids", required=True, help="comma-separated PIDs (app server, ollama)")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--chat", action="store_true", help="send one real chat during observation")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user")
    ap.add_argument("--password")
    args = ap.parse_args()
    pids = {int(p) for p in args.pids.split(",") if p.strip()}

    result: dict[str, object] = {"level": "L5 real-service observation (native profile)",
                                 "at": datetime.now(UTC).isoformat(), "host": platform.node(),
                                 "pids": sorted(pids), "limitations": LIMITATIONS}
    result["guard"] = guard_check()
    seen: dict[str, set[str]] = {}
    samples = 0
    deadline = time.monotonic() + args.seconds
    chat_result = None
    import threading

    if args.chat:
        holder: dict[str, str] = {}
        t = threading.Thread(target=lambda: holder.update(r=chat_once(args.base, args.user,
                                                                      args.password)))
        t.start()
    while time.monotonic() < deadline:
        for host, state, pid in snapshot():
            if pid in pids and is_external(host):
                seen.setdefault(host, set()).add(f"pid {pid} {state}")
        samples += 1
        time.sleep(args.interval)
    if args.chat:
        t.join(timeout=600)
        chat_result = holder.get("r", "did not finish")
    result.update(samples=samples, chat=chat_result,
                  external_connections={k: sorted(v) for k, v in seen.items()})
    passed = bool(result["guard"]["ok"]) and not seen  # type: ignore[index]
    result["passed"] = passed
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%S}"
    out = ROOT / "eval" / "results" / f"no-egress-native-{stamp}-{platform.node()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    print(f"\n{'PASS' if passed else 'FAIL'} (raw: {out.relative_to(ROOT)})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
