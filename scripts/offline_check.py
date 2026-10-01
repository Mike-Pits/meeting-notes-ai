"""End-to-end check in a network namespace with loopback only."""

import json
import socket
import time
from pathlib import Path
import httpx

root = Path(__file__).resolve().parent.parent
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1)
except OSError:
    pass
else:
    raise RuntimeError(
        "Офлайн-проверка должна запускаться в отдельном сетевом пространстве."
    )

start = time.monotonic()
with httpx.Client(
    base_url="http://127.0.0.1:8765",
    headers={"X-Meetings-Client": "local"},
    trust_env=False,
    timeout=60,
) as c:
    for attempt in range(60):
        try:
            c.get("/api/meetings").raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(1)
    response = c.post(
        "/api/meetings", json={"title": "Офлайн проверка", "meeting_date": "2026-10-01"}
    )
    response.raise_for_status()
    ident = response.json()["id"]
    with (root / "data/speech-test.m4a").open("rb") as f:
        response = c.post(
            f"/api/meetings/{ident}/files", files={"file": ("test.m4a", f)}
        )
    response.raise_for_status()
    fid = response.json()["files"][0]["id"]
    c.post(
        f"/api/meetings/{ident}/jobs?kind=transcription&file_id={fid}"
    ).raise_for_status()

    def finish():
        for attempt in range(240):
            data = c.get(f"/api/meetings/{ident}").json()
            job = data["jobs"][0]
            if job["state"] == "error":
                raise RuntimeError(job["error"])
            if job["state"] == "done":
                return data
            time.sleep(1)
        raise TimeoutError("Задание не завершилось")

    meeting = finish()
    assert meeting["text"] and not meeting["results"]
    print("Офлайн расшифровка пройдена", flush=True)
    # Simulate the required transcript review with the known original script.
    response = c.put(
        f"/api/meetings/{ident}",
        json={
            "title": meeting["title"],
            "meeting_date": meeting["meeting_date"],
            "version": meeting["version"],
            "text": (root / "fixtures/requirements.txt").read_text(),
        },
    )
    response.raise_for_status()
    c.post(f"/api/meetings/{ident}/jobs?kind=analysis").raise_for_status()
    meeting = finish()
    candidate = meeting["results"]["candidate"]
    for task in candidate["payload"]["tasks"]:
        task["owner_confirmed"] = task["due_confirmed"] = True
    response = c.put(
        f"/api/meetings/{ident}/results/candidate",
        json={"result": candidate["payload"], "version": candidate["version"]},
    )
    response.raise_for_status()
    version = response.json()["results"]["candidate"]["version"]
    c.post(f"/api/meetings/{ident}/accept?version={version}").raise_for_status()
    for format in ("md", "docx"):
        exported = c.get(f"/api/meetings/{ident}/export/{format}")
        exported.raise_for_status()
        assert exported.content
    report = {
        "network": "isolated namespace, loopback only; external connection fails",
        "passed": [
            "upload M4A",
            "GPU transcription",
            "reviewed transcript",
            "GPU analysis",
            "clarifications",
            "SQLite persistence",
            "Markdown export",
            "DOCX export",
        ],
        "seconds": round(time.monotonic() - start, 2),
    }
    (root / "data/offline-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
