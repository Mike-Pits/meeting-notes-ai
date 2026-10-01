#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS="$PWD/.models/ollama"
export OLLAMA_NO_CLOUD=1
mkdir -p data
ollama_pid=''
cleanup(){ if [ -n "$ollama_pid" ]; then kill "$ollama_pid" 2>/dev/null || true; fi; }
trap cleanup EXIT INT TERM
if ! curl -sf http://127.0.0.1:11435/api/version >/dev/null; then
  .runtime/ollama/bin/ollama serve >data/ollama-setup.log 2>&1 &
  ollama_pid=$!
  for attempt in $(seq 1 30); do
    if curl -sf http://127.0.0.1:11435/api/version >/dev/null; then break; fi
    sleep 1
  done
fi
curl --fail --silent --show-error http://127.0.0.1:11435/api/pull -d '{"model":"qwen3:8b","stream":false}'
HF_HOME="$PWD/.models/huggingface" .venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-medium', local_dir='.models/whisper-medium')"
printf '\nМодели загружены. Приложение запускается через scripts/run.sh\n'
