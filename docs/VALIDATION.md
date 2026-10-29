# Validating the full stack on a capable machine

Recommended machine: 16 GB+ RAM (32 GB comfortable), optional NVIDIA GPU with 8 GB+, 40 GB free
disk, network access for the first setup only. Record every result in `eval/results/` and
update `docs/hardware-profiles.md` and `docs/STATUS.md`.

## A. Native profile, full acceptance (L6)
```bash
git clone <repo> && cd On-prem-rag && git checkout phase-2/hardware-adaptive
# Prerequisites: uv, Ollama (set OLLAMA_NO_CLOUD=1 for its service), Node.js 22+
cd backend && uv sync --frozen --extra models && cd ..
cd frontend && npm ci && npm run build && npm test && cd ..
cd backend && uv run pytest -q                       # expect all pass, 1 skip without Tesseract
uv run airgap scan                                   # real hardware report + recommendation
uv run airgap --models-dir ../models doctor          # prerequisites
uv run airgap --data-dir data/acceptance --models-dir ../models serve
```
In the browser (http://127.0.0.1:8000): create the admin, run setup to the end. The last step
must say "real inference OK". Then, in another terminal:
```bash
# 1. Real model smoke (General Assistant through the kit's adapters)
uv run python ../eval/smoke/llm_runtime_smoke.py --model <active ollama tag> --num-ctx 4096
# 2. Quality, refusal, injection, ACL with real models (creates eval users)
AIRGAP_EVAL_USER=<admin> AIRGAP_EVAL_PASSWORD=<pw> uv run python ../eval/run_eval.py --adapter live --base http://127.0.0.1:8000/api
# 3. No-egress observation during real use (PIDs of `airgap serve` and Ollama)
uv run python ../eval/security/no_egress.py --pids <app>,<ollama...> --seconds 300 --chat --user <admin> --password <pw>
```
Then by hand, through the UI: upload `eval/datasets/corpus/*.md` -> ask "How often must the
P-100 pump be serviced?" (expect a citation to pump_manual.md) -> ask "Who won the 1998 World
Cup?" in the Document Assistant (expect a refusal) -> stop and restart `airgap serve` (history,
documents and the active model must persist) -> kill the server during an upload (it must
resume ingestion on restart) -> `airgap backup` / `airgap restore` onto a new `--data-dir`.

Capture peak memory (Task Manager / `top`), startup time, ingestion time and answer latency.

## B. Compose profile (L6), Linux with Docker Engine
```bash
bash deploy/dev/init_secrets.sh && bash deploy/dev/fetch_models.sh
docker compose -f deploy/docker-compose.yml --profile fetch run --rm ollama-fetch
docker compose -f deploy/docker-compose.yml up -d --build
cd backend && uv run python ../eval/smoke/phase1_e2e.py --base https://localhost:8443/api
bash ../deploy/security/no_egress_compose.sh --self-test    # must report the leak it creates
bash ../deploy/security/no_egress_compose.sh                # must pass
```
`phase1_e2e.py` still uses the `X-Dev-User` stub: keep `AIRGAP_DEV_AUTH=1` for that run.

## C. Offline bundle (L7)
On a connected machine: `deploy/build_bundle.sh --profile lite --out <dir> --key <pem>`.
Copy to a machine with networking **disabled**, run `airgap bundle verify`, install, and repeat A.
Also edit one byte of a bundle file and confirm verification fails.

## D. Firewall-level no-egress (strongest evidence)
Block all outbound traffic at the host firewall except loopback, run A again, and confirm every
function works. Record the firewall rules used.
