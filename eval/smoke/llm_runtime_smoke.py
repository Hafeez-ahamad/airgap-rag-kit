"""LIGHTWEIGHT REAL-SERVICE smoke test (validation level 5): the kit's own adapters against a
real local Ollama and a real model. Not a benchmark: one sample, no quality claims.

Checks:
  1. runtime reachable and model listed            (OllamaAdmin.installed)
  2. real inference check                           (OllamaAdmin.inference_check)
  3. General Assistant through the Orchestrator     (assistant mode, streaming, no retrieval)
  4. model unloaded afterwards                      (frees RAM/VRAM on small hosts)

Usage (from backend/):
  uv run python ../eval/smoke/llm_runtime_smoke.py --model qwen2.5:7b --num-ctx 2048
Writes raw results to eval/results/smoke-llm-<date>-<host>.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from airgap.chat.orchestrator import Orchestrator  # noqa: E402
from airgap.chat.routing import SingleModelRouter  # noqa: E402
from airgap.core.ports import GenerationOptions  # noqa: E402
from airgap.core.types import Mode  # noqa: E402
from airgap.llm_backends.ollama import OllamaBackend  # noqa: E402
from airgap.models_manager.runtime_admin import OllamaAdmin  # noqa: E402


class _NoRetrieval:
    """Assistant mode must never touch retrieval; this raises if it does."""

    def retrieve(self, *_a: object) -> None:
        raise AssertionError("General Assistant attempted retrieval")


def _gpu() -> str | None:
    import subprocess

    try:
        out = subprocess.run(  # noqa: S603
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],  # noqa: S607
            capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", required=True)
    ap.add_argument("--num-ctx", type=int, default=2048)
    args = ap.parse_args()
    results: dict[str, object] = {
        "level": "L5 lightweight real-service smoke (one sample, not a benchmark)",
        "at": datetime.now(UTC).isoformat(), "host": platform.platform(terse=True),
        "model": args.model, "num_ctx": args.num_ctx, "checks": {},
    }
    checks: dict[str, object] = results["checks"]  # type: ignore[assignment]
    admin = OllamaAdmin(args.url)
    ok = True
    try:
        tags = [m.tag for m in admin.installed()]
        checks["runtime_reachable"] = True
        checks["model_listed"] = args.model in tags
        ok &= args.model in tags
        results["gpu_before"] = _gpu()

        check = admin.inference_check(args.model, args.num_ctx)
        checks["inference_check"] = {"ok": check.ok, "seconds": round(check.seconds, 1),
                                     "reply": check.reply, "error": check.error}
        ok &= check.ok
        results["gpu_loaded"] = _gpu()

        llm = OllamaBackend(args.url, args.model)
        orch = Orchestrator(_NoRetrieval(), SingleModelRouter(llm),  # type: ignore[arg-type]
                            GenerationOptions(num_ctx=args.num_ctx, max_tokens=120))
        question = "In two sentences, what is retrieval-augmented generation?"
        started = time.monotonic()
        first_token: float | None = None
        tokens = 0
        final = None
        for event in orch.run(question, Mode.ASSISTANT, [], ["default"]):
            if event["type"] == "token":
                tokens += 1
                if first_token is None:
                    first_token = time.monotonic() - started
            elif event["type"] == "final":
                final = event["result"]
        assert final is not None
        checks["general_assistant"] = {
            "ok": bool(final.answer.strip()) and not final.citations_payload(),
            "question": question, "answer": final.answer,
            "stream_chunks": tokens, "seconds_to_first_chunk": round(first_token or -1, 1),
            "seconds_total": round(time.monotonic() - started, 1),
            "citations": final.citations_payload(),
        }
        ok &= bool(final.answer.strip())
    except Exception as exc:  # recorded, not hidden
        checks["error"] = f"{type(exc).__name__}: {exc}"
        ok = False
    finally:
        try:
            admin.unload(args.model)
            checks["unloaded"] = True
        except Exception as exc:
            checks["unloaded"] = f"failed: {type(exc).__name__}"
    results["passed"] = ok
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"smoke-llm-{datetime.now(UTC):%Y%m%d}-{socket.gethostname()}.json"
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=1, ensure_ascii=False))
    print(f"\n{'PASS' if ok else 'FAIL'} - raw results in {out.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
