import json
import time
from pathlib import Path
from app.ai import analyze

root = Path(__file__).resolve().parent.parent
report = []
for name in ("requirements", "progress", "acceptance", "short"):
    start = time.monotonic()
    result = analyze(
        (root / "fixtures" / f"{name}.txt").read_text(), "2026-10-01", print
    )
    report.append(
        {
            "fixture": name,
            "seconds": round(time.monotonic() - start, 2),
            "result": result.model_dump(mode="json"),
        }
    )
    (root / "data" / "ai-benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(name, report[-1]["seconds"], flush=True)
