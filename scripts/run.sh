#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS="$PWD/.models/ollama"
export OLLAMA_NO_CLOUD=1
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:$PWD/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:${LD_LIBRARY_PATH:-}"
mkdir -p data
umask 077
pids=()
cleanup(){ for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
if ! curl -sf http://127.0.0.1:11435/api/version >/dev/null; then
  .runtime/ollama/bin/ollama serve >data/ollama.log 2>&1 &
  pids+=("$!")
fi
ready=0
for attempt in $(seq 1 30); do
  if curl -sf http://127.0.0.1:11435/api/version >/dev/null; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  printf 'Ollama не запустился. Проверьте data/ollama.log\n' >&2
  exit 1
fi
.venv/bin/python -m app.worker >data/worker.log 2>&1 &
pids+=("$!")
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 &
pids+=("$!")
printf 'Откройте http://127.0.0.1:8765\nОстановка: Ctrl+C\n'
wait -n "${pids[@]}"
