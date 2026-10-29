"""LIGHTWEIGHT REAL-SERVICE smoke (L5): load the platform retrieval models in-process exactly as
the native profile does (offline, local files only), measure process memory, run a sanity check.

Usage (from backend/, after `uv sync --extra models`):
  uv run python ../eval/smoke/retrieval_models_smoke.py --models-dir ../models
Writes eval/results/smoke-retrieval-<timestamp>-<host>.json. One sample; not a benchmark.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from airgap.adapters.local_models import LocalModels  # noqa: E402
from airgap.capacity.catalog import load_catalog  # noqa: E402
from airgap.setup.installer import retrieval_dir  # noqa: E402


def rss_bytes() -> int | None:
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),  # noqa: RUF012
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        c = Counters()
        c.cb = ctypes.sizeof(Counters)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p  # pseudo-handle -1: keep all 64 bits
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters),
                                               ctypes.c_ulong]
        ok = psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return int(c.PeakWorkingSetSize) if ok else None
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="../models")
    ap.add_argument("--dtype", default="bfloat16")
    args = ap.parse_args()
    r = load_catalog().retrieval
    models_dir = Path(args.models_dir).resolve()
    local = LocalModels(retrieval_dir(models_dir, r.embedder), retrieval_dir(models_dir, r.reranker),
                        "cpu", args.dtype)
    base = rss_bytes()
    t0 = time.monotonic()
    vecs = local.encode(["The pump must be serviced every 500 operating hours.",
                         "पंप को हर 500 घंटे में सर्विस करना चाहिए।",
                         "The cafeteria closes at 3 pm on Fridays."])
    load_s = time.monotonic() - t0
    scores = local.predict([("How often is the pump serviced?", "The pump must be serviced every 500 operating hours."),
                            ("How often is the pump serviced?", "The cafeteria closes at 3 pm on Fridays.")])
    peak = rss_bytes()
    result = {
        "level": "L5 lightweight real-service smoke (one sample, not a benchmark)",
        "at": datetime.now(UTC).isoformat(), "host": platform.node(), "dtype": args.dtype,
        "embedder": f"{r.embedder.name}@{r.embedder.revision[:7]}",
        "reranker": f"{r.reranker.name}@{r.reranker.revision[:7]}",
        "embedding_dim": len(vecs[0]),
        "load_and_first_embed_seconds": round(load_s, 1),
        "process_rss_before_bytes": base, "process_peak_rss_bytes": peak,
        "cosine_en_hi_same_meaning": round(cos(vecs[0], vecs[1]), 3),
        "cosine_en_unrelated": round(cos(vecs[0], vecs[2]), 3),
        "rerank_relevant": round(scores[0], 3), "rerank_irrelevant": round(scores[1], 3),
    }
    ok = (result["cosine_en_hi_same_meaning"] > result["cosine_en_unrelated"]
          and scores[0] > scores[1])
    result["passed"] = ok
    out = ROOT / "eval" / "results" / f"smoke-retrieval-{datetime.now(UTC):%Y%m%dT%H%M%S}-{platform.node()}.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=1, ensure_ascii=False))
    print(f"\n{'PASS' if ok else 'FAIL'} (raw: {out.relative_to(ROOT)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
