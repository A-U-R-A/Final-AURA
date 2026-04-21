#!/usr/bin/env bash
# ============================================================
#  AURA — Linux / macOS Launcher
#  Checks Python, creates/activates venv, installs deps,
#  trains missing models, then starts the server.
# ============================================================

set -euo pipefail

# ── Resolve AURA root (two levels up from this script) ──────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AURA_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${AURA_DIR}/.venv"
REQ_FILE="${AURA_DIR}/requirements.txt"
MODELS_DIR="${AURA_DIR}/models"
HASH_FILE="${VENV_DIR}/.req_hash"

# ── Colour helpers ───────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "    ${GREEN}${*}${RESET}"; }
warn()    { echo -e "    ${YELLOW}${*}${RESET}"; }
fatal()   { echo -e "\n  ${RED}[ERROR]${RESET} ${*}\n"; exit 1; }
section() { echo -e "\n${BOLD}${CYAN}[${1}]${RESET} ${2}"; }

echo ""
echo -e "${BOLD}  =========================================="
echo -e "   AURA  --  ECLSS Predictive Maintenance"
echo -e "  ==========================================${RESET}"
echo ""

# ── 1. Locate Python 3.10+ ──────────────────────────────────
section "1/6" "Checking Python installation..."

PYTHON_CMD=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PY_VER=$("$candidate" --version 2>&1 | awk '{print $2}')
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
            PYTHON_CMD="$candidate"
            info "Found: Python ${PY_VER} (${candidate})"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    fatal "Python 3.10+ not found.\n\n\
  Ubuntu/Debian:  sudo apt install python3.11 python3.11-venv\n\
  Fedora/RHEL:    sudo dnf install python3.11\n\
  macOS (Homebrew): brew install python@3.11\n\
  Or download from https://www.python.org/downloads/"
fi

# ── 2. Create / activate virtualenv ─────────────────────────
section "2/6" "Checking virtual environment..."

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    info "Creating .venv at ${VENV_DIR}"
    "$PYTHON_CMD" -m venv "${VENV_DIR}" || \
        fatal "venv creation failed.\n  Try: sudo apt install python3.11-venv"
    info "Created."
else
    info "Found existing .venv"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
info "Activated."

PYTHON_CMD="python"   # use venv python from here on
export PYTHONIOENCODING=utf-8

# ── 3. Install / upgrade dependencies ───────────────────────
section "3/6" "Installing dependencies from requirements.txt..."

[ -f "$REQ_FILE" ] || fatal "requirements.txt not found at ${REQ_FILE}"

# Hash-based skip: only reinstall when requirements.txt changes
CURRENT_HASH=""
if command -v sha256sum &>/dev/null; then
    CURRENT_HASH=$(sha256sum "$REQ_FILE" | awk '{print $1}')
elif command -v shasum &>/dev/null; then
    CURRENT_HASH=$(shasum -a 256 "$REQ_FILE" | awk '{print $1}')
fi

STORED_HASH=""
[ -f "$HASH_FILE" ] && STORED_HASH=$(cat "$HASH_FILE")

if [ -n "$CURRENT_HASH" ] && [ "$CURRENT_HASH" = "$STORED_HASH" ]; then
    info "Requirements unchanged, skipping install."
else
    info "Installing/updating packages..."
    "$PYTHON_CMD" -m pip install --upgrade pip --quiet
    "$PYTHON_CMD" -m pip install -r "$REQ_FILE"
    echo "$CURRENT_HASH" > "$HASH_FILE"
    info "Done."
fi

# ── 4. Train missing models ──────────────────────────────────
section "4/6" "Checking ML models..."

NEEDS_TRAINING=0
for model in isolationForestModel.joblib randomForestModel.joblib lstmModel.pt dqnModel.pt; do
    if [ ! -f "${MODELS_DIR}/${model}" ]; then
        warn "Missing: ${model}"
        NEEDS_TRAINING=1
    fi
done

if [ "$NEEDS_TRAINING" -eq 1 ]; then
    info "Training missing models (this may take a few minutes)..."
    cd "$AURA_DIR"

    if [ ! -f "${MODELS_DIR}/isolationForestModel.joblib" ]; then
        info "-- Training Isolation Forest..."
        "$PYTHON_CMD" -m scripts.train_isolation_forest \
            || fatal "Isolation Forest training failed."
    fi

    if [ ! -f "${MODELS_DIR}/randomForestModel.joblib" ]; then
        info "-- Training Random Forest..."
        "$PYTHON_CMD" -m scripts.train_random_forest \
            || fatal "Random Forest training failed."
    fi

    if [ ! -f "${MODELS_DIR}/lstmModel.pt" ]; then
        info "-- Training LSTM (patience required)..."
        "$PYTHON_CMD" -m scripts.train_lstm \
            || fatal "LSTM training failed."
    fi

    if [ ! -f "${MODELS_DIR}/dqnModel.pt" ]; then
        info "-- Training DQN (patience required)..."
        "$PYTHON_CMD" -m scripts.train_dqn \
            || fatal "DQN training failed."
    fi

    info "All models trained."
else
    info "All models present."
fi

# ── 5. Run test suite ────────────────────────────────────────
section "5/6" "Running pre-flight tests..."

cd "$AURA_DIR"
if "$PYTHON_CMD" -m pytest tests/ -v --tb=short -q 2>&1; then
    info "All tests passed."
else
    warn "Some tests failed — check output above."
    warn "Server will still start, but investigate failures before production use."
fi

# ── 6. Start AURA server ─────────────────────────────────────
section "6/6" "Starting AURA server..."

echo ""
echo -e "  ${BOLD}Access the dashboard at:${RESET}  ${CYAN}http://localhost:8000${RESET}"
echo    "  Press Ctrl+C to stop."
echo    "  ------------------------------------------"
echo ""

cd "$AURA_DIR"
exec "$PYTHON_CMD" -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
