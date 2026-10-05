#!/usr/bin/env bash
# AgriGuard Easy Runner - No repeated pip installs! No Docker!

set -e

echo "🚀 AgriGuard Launcher (native, no Docker)"

# Prefer Python 3.12/3.13 because the pinned scientific packages have
# compatible wheels there. On Python 3.14, scikit-learn 1.5.0 falls back to a
# lengthy source build and can make the launcher appear hung.
PYTHON_BIN=""
for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[ -n "$PYTHON_BIN" ] || PYTHON_BIN="python"

PYTHON_VERSION=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ "$PYTHON_VERSION" == "3.14" ]]; then
    echo "⚠️  Python 3.14 detected. Installing with this interpreter may build pinned ML packages from source."
    echo "   Install Python 3.12 or 3.13 for the fastest and most reliable setup."
fi

# Cross-platform sha256
sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Use a version-specific environment when the existing .venv was created with
# another Python version. This avoids silently reusing a broken 3.14 venv.
VENV_DIR=".venv-${PYTHON_VERSION}"
if [ -x ".venv/bin/python" ] && [ "$(".venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = "$PYTHON_VERSION" ]; then
    VENV_DIR=".venv"
fi

# Virtual env
if [ ! -d "$VENV_DIR" ]; then
    echo "→ Creating virtual environment..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Smart dependency install (hash lives INSIDE .venv so a deleted venv forces reinstall)
if [ -f "requirements.txt" ]; then
    HASH_FILE="$VENV_DIR/.requirements.hash"
    CURRENT_HASH="${PYTHON_VERSION}:$(sha256 requirements.txt)"

    if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$CURRENT_HASH" ] || ! python -c "import fastapi, pandas, sklearn, streamlit" >/dev/null 2>&1; then
        echo "→ Installing dependencies (one-time or after changes)..."
        pip install --upgrade pip
        pip install -r requirements.txt
        echo "$CURRENT_HASH" > "$HASH_FILE"
    else
        echo "✅ Dependencies up to date."
    fi
fi

# Env file
if [ -f "config/.env.example" ] && [ ! -f "config/.env" ]; then
    cp config/.env.example config/.env
    echo "⚠️  Created config/.env — edit it with your DB/API keys!"
fi

# Export config/.env into THIS shell so standalone scripts (train_models.py,
# download_wfp_data.py) see the same vars the FastAPI app gets via
# python-dotenv in config.py — no more manual `export AGRIGUARD_PRICE_DATA=...`
if [ -f "config/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source config/.env
    set +a
fi

# Resolve paths with the same defaults config.py/train_models.py use, so
# the checks below agree with what the app will actually load.
PRICE_DATA="${AGRIGUARD_PRICE_DATA:-data/raw/wfp_food_prices_uga.csv}"
MODEL_DIR="${MODEL_DIR:-ml/models}"
FORCE_SETUP=false
[[ "$1" == "--setup" || "$1" == "-s" ]] && FORCE_SETUP=true

# Data bootstrap — one-time download (or synthetic fallback) for a fresh
# checkout / empty data/raw/. Safe to skip once the CSV exists; wfp_sync.py
# keeps it fresh from then on.
if [ -f "scripts/download_wfp_data.py" ] && { [ ! -f "$PRICE_DATA" ] || [ "$FORCE_SETUP" = true ]; }; then
    echo "→ Downloading WFP price data..."
    python scripts/download_wfp_data.py
fi

# Model bootstrap — train once if the pickles are missing, so `./run.sh`
# alone (no flags, no prior manual steps) gets you a working forecast API.
if [ -f "scripts/train_models.py" ] && { [ ! -f "$MODEL_DIR/price_forecast_model.pkl" ] || [ ! -f "$MODEL_DIR/encoders.pkl" ] || [ "$FORCE_SETUP" = true ]; }; then
    echo "→ Training models (missing or --setup forced)..."
    python scripts/train_models.py --data "$PRICE_DATA" --out "$MODEL_DIR"
fi

echo "✅ Ready!"
echo "Dashboard → http://localhost:8501"
echo "API docs  → http://localhost:8000/docs"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "🐍 Starting backend (uvicorn)..."
UVICORN_ARGS=(backend.app.main:app --host 0.0.0.0 --port 8000)
if [ "${AGRIGUARD_RELOAD:-false}" = "true" ]; then
    UVICORN_ARGS+=(--reload)
    echo "   Hot reload enabled via AGRIGUARD_RELOAD=true"
fi
uvicorn "${UVICORN_ARGS[@]}" &
BACKEND_PID=$!

# Wait for backend health before starting frontend
echo "⏳ Waiting for backend to become healthy..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo "✅ Backend is up."
        break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "❌ Backend process died. Check the logs above."
        exit 1
    fi
    sleep 1
done

echo "📊 Starting frontend (streamlit)..."
(cd frontend && streamlit run Home.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true) &
FRONTEND_PID=$!

# Wait on both — if either exits, cleanup() takes down the other via trap
wait -n "$BACKEND_PID" "$FRONTEND_PID"