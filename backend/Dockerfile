# syntax=docker/dockerfile:1
# Targets: api, worker, models. Built on a connected machine; images ship in the offline bundle.
# TODO(verify): pin base images by digest in Phase 4 (bundle build).

FROM python:3.12.16-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    PATH=/app/.venv/bin:$PATH
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin airgap
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM base AS app-deps
RUN uv sync --frozen --no-dev --no-install-project
COPY airgap ./airgap
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

FROM app-deps AS api
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory airgap.bootstrap:asgi --host 0.0.0.0 --port 8000 --no-server-header"]

FROM app-deps AS worker
USER root
# Tesseract plus language packs (English, Hindi). Add more packs here; see docs/runbook.md.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
 && rm -rf /var/lib/apt/lists/*
USER 10001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/airgap-worker-heartbeat') < 60 else 1)"]
CMD ["python", "-m", "airgap.bootstrap"]

FROM base AS models
RUN uv sync --frozen --no-dev --extra models --no-install-project
COPY airgap ./airgap
RUN uv sync --frozen --no-dev --extra models
USER 10001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=10 \
  CMD ["python", "-c", "import urllib.request,json,sys; r=json.load(urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)); sys.exit(0 if r['status']=='ok' else 1)"]
CMD ["uvicorn", "--factory", "airgap.model_service.app:create_app", "--host", "0.0.0.0", "--port", "8001", "--no-server-header"]
