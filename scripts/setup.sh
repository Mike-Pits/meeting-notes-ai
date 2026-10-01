#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
mkdir -p .runtime/ollama .models
if [ ! -x .runtime/ollama/bin/ollama ]; then
  curl -fL --retry 2 https://github.com/ollama/ollama/releases/download/v0.35.0/ollama-linux-amd64.tar.zst -o .runtime/ollama.tar.zst
  tar --zstd -xf .runtime/ollama.tar.zst -C .runtime/ollama
fi
printf 'Зависимости установлены. Далее выполните scripts/download-models.sh\n'
