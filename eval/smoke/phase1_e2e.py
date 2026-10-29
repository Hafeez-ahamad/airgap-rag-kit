"""Phase 1 end-to-end acceptance run against a live stack (through the HTTPS proxy).

Usage (from the repo root, stack running):
    cd backend && uv run python ../eval/smoke/phase1_e2e.py --base https://localhost:8443/api

Prints one line per check plus the raw answers, and exits non-zero if any check fails.
This is a functional smoke test, not a quality evaluation: it reports no accuracy numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from tests.fixture_files import docx_file, scanned_pdf, text_pdf, xlsx_file  # noqa: E402

USER = {"X-Dev-User": "e2e"}
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}", flush=True)


def upload(client: httpx.Client, files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    resp = client.post(
        "/documents",
        files=[("files", (Path(n).name, d)) for n, d in files],
        data={"paths": [n for n, _ in files]},
    )
    resp.raise_for_status()
    results: list[dict[str, Any]] = resp.json()["results"]
    return results


def wait_ready(client: httpx.Client, names: set[str], timeout_s: float) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    docs: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        docs = {d["name"]: d for d in client.get("/documents").json()}
        states = [docs.get(n, {}).get("status") for n in names]
        if all(s in {"ready", "failed"} for s in states):
            return docs
        time.sleep(3)
    return docs


def ask(client: httpx.Client, question: str, mode: str = "grounded", conv: str | None = None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    started = time.monotonic()
    with client.stream(
        "POST", "/chat", json={"question": question, "mode": mode, "conversation_id": conv}
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    final = events[-1]
    final["_meta"] = events[0] if events and events[0]["type"] == "meta" else {}
    final["_retries"] = sum(e["type"] == "retry" for e in events)
    final["_seconds"] = round(time.monotonic() - started, 1)
    print(f"    Q: {question}\n    A: {json.dumps(final.get('answer'), ensure_ascii=False)}")
    for c in final.get("citations", []):
        print(f"       [{c['n']}] {c['doc_name']} p={c['page']} sec={c['section']!r} valid={c['valid']}")
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://localhost:8443/api")
    parser.add_argument("--max-upload-mb", type=int, default=50)
    parser.add_argument("--ingest-timeout", type=float, default=900)
    args = parser.parse_args()
    # Self-signed dev certificate: verification off for this local smoke test only.
    client = httpx.Client(base_url=args.base, headers=USER, verify=False, timeout=600)  # noqa: S501

    health = client.get("/health").json()
    check("health ok", health["status"] == "ok", json.dumps(health["components"]))

    corpus = [
        ("e2e/pump_manual.pdf", text_pdf([
            ["1 Maintenance", "The P-100 pump must be serviced every 500 operating hours.",
             "Use ISO VG 46 hydraulic oil only."],
            ["2 Safety", "Isolate the electrical supply before opening the pump housing."],
        ])),
        ("e2e/leave_policy.docx", docx_file(
            "Leave policy", ["Full-time staff receive 24 days of paid annual leave."],
            [["Grade", "Carry-over days"], ["A", "5"], ["B", "8"]],
        )),
        ("e2e/scanned_notice.pdf", scanned_pdf(["NOTICE", "The cafeteria closes at 3 pm on Fridays."])),
        ("e2e/parts.csv", b"part,stock\nvalve V-12,40\ngasket G-7,125\n"),
        ("e2e/budget.xlsx", xlsx_file({"FY2026": [["team", "budget"], ["Facilities", 182000]]})),
        ("e2e/folder/notes.md", b"# Warranty\n\nThe Premium warranty covers labour for 36 months.\n"),
    ]
    results = upload(client, corpus)
    check("all fixture files accepted", all(r["outcome"] == "queued" for r in results),
          ", ".join(f"{r['filename']}={r['outcome']}" for r in results))

    bad = upload(client, [
        ("e2e/huge.txt", b"a" * (args.max_upload_mb * 1024 * 1024 + 1)),
        ("e2e/tool.exe", b"MZ\x90\x00"),
    ])
    check("oversized file rejected with reason", bad[0]["outcome"] == "rejected"
          and "larger than" in (bad[0]["message"] or ""), bad[0]["message"] or "")
    check("disallowed type rejected with reason", bad[1]["outcome"] == "rejected"
          and "not allowed" in (bad[1]["message"] or ""), bad[1]["message"] or "")

    started = time.monotonic()
    docs = wait_ready(client, {n for n, _ in corpus}, args.ingest_timeout)
    for name, _ in corpus:
        d = docs.get(name, {})
        check(f"{name} reaches ready", d.get("status") == "ready",
              f"status={d.get('status')} error={d.get('error')} chunks={d.get('chunk_count')} "
              f"ocr_pages={d.get('ocr_pages')}")
    print(f"    ingestion wall time: {time.monotonic() - started:.0f}s (single run, not a benchmark)")

    a = ask(client, "How often must the P-100 pump be serviced?")
    check("answerable: cited, not refused", not a["refused"] and any(c["valid"] for c in a["citations"]),
          f"flagged={a['flagged']} retries={a['_retries']} {a['_seconds']}s")
    check("answerable: citation has page", any(c["page"] for c in a["citations"] if c["valid"]))

    s = ask(client, "What time does the cafeteria close on Fridays?")
    check("scanned (OCR) content answerable", not s["refused"] and
          any(c["doc_name"] == "e2e/scanned_notice.pdf" for c in s["citations"] if c["valid"]))

    u = ask(client, "Who won the football World Cup in 1998?")
    check("unanswerable: refused", u["refused"], u["answer"])

    g = ask(client, "Who won the football World Cup in 1998?", mode="general")
    check("general mode: answers, labels general knowledge",
          not g["refused"] and any(not seg["grounded"] for seg in g["segments"]))

    first = ask(client, "What does the Premium warranty cover?")
    follow = ask(client, "and for how many months?", conv=first["conversation_id"])
    check("follow-up rewritten", bool(follow["_meta"].get("rewritten")), follow["_meta"].get("query", ""))
    check("follow-up answered with citation", not follow["refused"] and any(c["valid"] for c in follow["citations"]))

    same = upload(client, [corpus[0]])[0]
    check("unchanged re-upload is a no-op", same["outcome"] == "unchanged", same["outcome"])
    changed_pdf = text_pdf([["1 Maintenance", "The P-100 pump must be serviced every 750 operating hours."]])
    changed = upload(client, [("e2e/pump_manual.pdf", changed_pdf)])[0]
    check("changed file becomes new version", changed["outcome"] == "new_version" and changed["version"] == 2,
          f"{changed['outcome']} v{changed['version']}")
    wait_ready(client, {"e2e/pump_manual.pdf"}, args.ingest_timeout)
    time.sleep(2)
    v2 = ask(client, "How often must the P-100 pump be serviced?")
    quotes = " ".join(c["quote"] for c in v2["citations"])
    check("old version gone, new version cited", "750" in quotes and "500" not in quotes, quotes)

    fb = client.post("/feedback", json={"message_id": a["message_id"], "rating": "up"})
    check("feedback stored", fb.status_code == 201)
    hist = client.get("/chat/history").json()
    check("history lists conversations", len(hist) >= 5, f"{len(hist)} conversations")
    other = client.get("/chat/history", headers={"X-Dev-User": "someone-else"}).json()
    check("history isolated per user", other == [])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
