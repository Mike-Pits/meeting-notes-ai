#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ip link set lo up
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS="$PWD/.models/ollama"
export OLLAMA_NO_CLOUD=1
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:$PWD/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:${LD_LIBRARY_PATH:-}"
export MEETINGS_DATA
MEETINGS_DATA=$(mktemp -d "$PWD/.runtime/offline-data-XXXXXX")
pids=()
cleanup(){ for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
.runtime/ollama/bin/ollama serve >"$MEETINGS_DATA/ollama.log" 2>&1 &
pids+=("$!")
.venv/bin/python -m app.worker >"$MEETINGS_DATA/worker.log" 2>&1 &
pids+=("$!")
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 >"$MEETINGS_DATA/server.log" 2>&1 &
pids+=("$!")
PYTHONPATH="$PWD" .venv/bin/python scripts/offline_check.py
