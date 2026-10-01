"""Verify agreed facts on the fixed synthetic reference set, not arbitrary meetings."""

import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
expected = json.loads((root / "fixtures/expected.json").read_text())
rows = json.loads((root / "data/ai-benchmark.json").read_text())
assert {r["fixture"] for r in rows} == set(expected)
for row in rows:
    name = row["fixture"]
    result = row["result"]
    e = expected[name]
    assert len(result["decisions"]) == e["decisions"], name
    assert len(result["tasks"]) == e["tasks"], name
    assert [t["owner"] for t in result["tasks"]] == e["owners"], name
    assert [t["due_date"] for t in result["tasks"]] == e["due_dates"], name
    text = (root / "fixtures" / f"{name}.txt").read_text()
    assert all(x["quote"] in text for x in result["decisions"] + result["tasks"]), name
    print(
        f'{name}: проверены количество, ответственные, сроки и цитаты; {row["seconds"]} с'
    )
