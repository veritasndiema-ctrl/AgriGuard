# ─────────────────────────────────────────────────────────────────────────────
# AgriGuard backend Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# ── deps layer (cached unless requirements.txt changes) ──────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── source ────────────────────────────────────────────────────────────────────
COPY backend/  ./backend/
COPY scripts/  ./scripts/
COPY config/   ./config/

# create empty dirs so volumes mount cleanly
RUN mkdir -p data/raw ml/models

# default: run the API (override in docker-compose for trainer)
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]