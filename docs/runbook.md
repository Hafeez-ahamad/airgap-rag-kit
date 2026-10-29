# Runbook

Two deployment profiles (ADR-0009):
- **native**: one process, no Docker/WSL. Laptops, desktops, small servers. Recommended default.
- **compose**: containers (Postgres, Qdrant, Valkey, model service, Ollama, nginx/TLS). Linux
  servers with 16 GiB+ RAM and Docker Engine.

All sizes and memory figures the app shows are **estimates** until a real inference test passes.

## 1. Install (native)
Prerequisites (the app checks them; it never installs system software for you):
`uv`, Ollama, and Node.js 22+ once to build the UI (or use a bundle with a prebuilt UI).

```bash
scripts/bootstrap.sh --with-document-assistant      # Linux/macOS
```
```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -WithDocumentAssistant   # Windows
```
The browser opens at http://127.0.0.1:8000. Create the admin account (only possible from the
same machine), then follow setup: device scan (read-only) -> requirements -> model -> dry-run
plan -> install -> real inference test. Setup resumes after a restart or reboot.

Without `--with-document-assistant` only the General Assistant can be completed (no PyTorch).

**Privacy on the native profile:** set `OLLAMA_NO_CLOUD=1` for the Ollama service and turn off
Ollama's automatic updates. A native Ollama install on the dev machine was observed connecting
to a public address and updated itself (0.40.12 -> 0.40.13) during testing.

## 2. Install (compose)
```bash
bash deploy/dev/init_secrets.sh
docker compose -f deploy/docker-compose.yml up -d --build      # first build needs network
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml up -d  # NVIDIA
```
Run `airgap scan` on the **host** first: inside the API container the probe cannot see the GPU.
Block outbound traffic on the host firewall as well; the compose `backend` network is internal.

## 3. Offline install from a bundle
On a connected build machine:
```bash
uv run --directory backend airgap bundle keygen ~/airgap.pem ~/airgap.pub   # once; keep .pem private
deploy/build_bundle.sh --profile lite --out /media/usb/airgap-bundle --key ~/airgap.pem
```
On the target (no network):
```bash
uv run --directory backend airgap bundle verify /media/usb/airgap-bundle --public-key airgap.pub
```
Then copy `models/retrieval/*` to `models/`, import the Ollama store, and run setup. Setup reuses
installed models and only verifies them. **Not yet validated end to end** (see docs/STATUS.md).

## 4. Day-2 operations (native)
| Task | Command |
|---|---|
| Health and prerequisites | `airgap doctor` |
| Re-scan hardware | Settings -> "Re-scan and change model", or `airgap scan` |
| Recommendation for another device | Admin -> Compatibility Lab, or `airgap recommend --profile ID` / `--spec file.json` |
| Add a user | Admin -> Users, or `airgap user create NAME --role editor --groups hr` |
| Backup (stop the app first) | `airgap --data-dir data/app backup backup.tar.gz` |
| Restore onto a clean directory | `airgap --data-dir data/app restore backup.tar.gz` |
| Restore over existing data | add `--force` (old data kept as `data/app.old-<timestamp>`) |
| Audit export | Admin -> Audit log -> Export, or `GET /api/admin/audit/export` |
| Diagnostics for a bug report | Settings -> Diagnostics -> Export (paths and secrets redacted) |

Backups are **not encrypted**: they contain documents and chat history. Store them on
encrypted media.

## 5. Uninstall and data retention
1. Stop the app. 2. Optional: `airgap backup` first. 3. Delete the data directory
(`backend/data/app` by default: database, index, uploaded files, setup state). 4. Model weights
live in `models/` and the Ollama store; remove with `ollama rm <tag>`. Nothing is deleted
automatically.

## 6. Troubleshooting
| Symptom | Check |
|---|---|
| "Local model unavailable" | Ollama running? `ollama list` shows the active model? `airgap doctor` |
| Setup blocked on PyTorch | `uv sync --extra models` in `backend/` (about 1 GB) |
| Scanned PDF has no text | install Tesseract with language packs (Settings shows which are missing); set `AIRGAP_OCR_BINARY` if it is installed somewhere unusual; re-upload |
| Very slow answers | model is split GPU/CPU or CPU-only (shown in setup); choose a smaller model |
| Out of memory | the planner's "fits now" warning: close applications or choose a smaller model |
| Backup refuses | the app is still running (embedded index lock) |
| Docker Desktop returns HTTP 500 | restart Docker Desktop; consider the native profile |
| TLS certificate (compose) | replace `deploy/secrets/tls/server.{crt,key}` and restart the proxy |
