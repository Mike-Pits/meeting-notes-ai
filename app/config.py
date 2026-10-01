import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MEETINGS_DATA", ROOT / "data")).resolve()
MODELS = ROOT / ".models"
OLLAMA_URL = "http://127.0.0.1:11435"
MODEL = os.environ.get("MEETINGS_MODEL", "qwen3:8b")
WHISPER = MODELS / "whisper-medium"
TEXT_LIMIT = 200_000
AUDIO_LIMIT = 500_000_000
DOCUMENT_LIMIT = 50_000_000
