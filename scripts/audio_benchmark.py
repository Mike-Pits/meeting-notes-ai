import json
import time
from pathlib import Path
from app.ai import transcribe

root = Path(__file__).resolve().parent.parent
start = time.monotonic()
text = transcribe(root / "data/speech-test.wav", print)
report = {
    "source": "synthetic Russian speech, espeak-ng",
    "duration_seconds": 38.395,
    "processing_seconds": round(time.monotonic() - start, 2),
    "text": text,
}
(root / "data/audio-benchmark.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False, indent=2))
