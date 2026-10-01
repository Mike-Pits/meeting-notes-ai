import io
import json
from datetime import date
import pytest
from docx import Document
from fastapi.testclient import TestClient
from app import db, main, worker, ai
from app.schemas import Result, Task, Decision

HEADERS = {"X-Meetings-Client": "local"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(worker, "DATA", tmp_path)
    with TestClient(main.app, headers=HEADERS) as client:
        yield client


def create(client, text="Анна подготовит отчёт завтра."):
    response = client.post(
        "/api/meetings",
        json={"title": "Тестовая встреча", "meeting_date": "2026-10-01", "text": text},
    )
    assert response.status_code == 201, response.text
    return response.json()


def fake_result(text):
    return Result(
        summary="Обсудили отчёт", tasks=[Task(text="Подготовить отчёт", quote=text)]
    )


def candidate(client, monkeypatch):
    m = create(client)
    monkeypatch.setattr(ai, "analyze", lambda text, day, progress: fake_result(text))
    assert client.post(f"/api/meetings/{m['id']}/jobs?kind=analysis").status_code == 200
    assert worker.process_one()
    return client.get("/api/meetings/" + m["id"]).json()


def test_draft_conflict_persistence(client):
    m = create(client)
    body = {
        "title": "Обновлено",
        "meeting_date": "2026-10-02",
        "text": "Новый текст",
        "version": m["version"],
    }
    assert client.put("/api/meetings/" + m["id"], json=body).status_code == 200
    assert client.put("/api/meetings/" + m["id"], json=body).status_code == 409
    db.initialize()
    assert client.get("/api/meetings/" + m["id"]).json()["text"] == "Новый текст"


def test_document_import(client):
    m = create(client, "")
    for name, raw in [("notes.txt", "Привет мир".encode())]:
        response = client.post(
            f"/api/meetings/{m['id']}/files", files={"file": (name, raw)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "Привет мир"
    doc = Document()
    doc.add_paragraph("Первый абзац")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "Таблица"
    doc.add_paragraph("Последний абзац")
    out = io.BytesIO()
    doc.save(out)
    response = client.post(
        f"/api/meetings/{m['id']}/files", files={"file": ("notes.docx", out.getvalue())}
    )
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "Первый абзац\nТаблица\nПоследний абзац"
    for name, raw in [
        ("bad.docx", b"bad"),
        ("bad.pdf", b"bad"),
        ("empty.txt", b""),
        ("bad.txt", b"\x00"),
        ("bad.mp3", b"bad"),
        ("x.exe", b"abc"),
    ]:
        assert (
            client.post(
                f"/api/meetings/{m['id']}/files", files={"file": (name, raw)}
            ).status_code
            == 400
        )


def test_accept_clarifications_export_replace(client, monkeypatch):
    m = candidate(client, monkeypatch)
    ident = m["id"]
    r = m["results"]["candidate"]
    assert client.post(f"/api/meetings/{ident}/accept?version=0").status_code == 400
    t = r["payload"]["tasks"][0]
    t.update(owner_confirmed=True, due_confirmed=True)
    saved = client.put(
        f"/api/meetings/{ident}/results/candidate",
        json={"result": r["payload"], "version": 0},
    )
    assert saved.status_code == 200
    assert client.post(f"/api/meetings/{ident}/accept?version=0").status_code == 409
    accepted = client.post(f"/api/meetings/{ident}/accept?version=1").json()
    old = accepted["results"]["current"]
    old["payload"]["tasks"][0]["status"] = "Выполнено"
    assert (
        client.put(
            f"/api/meetings/{ident}/results/current",
            json={"result": old["payload"], "version": old["version"]},
        ).status_code
        == 200
    )
    assert "Выполнено" in client.get(f"/api/meetings/{ident}/export/md").text
    response = client.get(f"/api/meetings/{ident}/export/docx")
    assert response.status_code == 200
    assert (
        Document(io.BytesIO(response.content)).paragraphs[0].text == "Тестовая встреча"
    )
    client.post(f"/api/meetings/{ident}/jobs?kind=analysis")
    worker.process_one()
    assert (
        client.get(f"/api/meetings/{ident}").json()["results"]["current"]["payload"][
            "tasks"
        ][0]["status"]
        == "Выполнено"
    )
    client.delete(f"/api/meetings/{ident}/candidate")
    assert (
        client.get(f"/api/meetings/{ident}").json()["results"]["current"]["payload"][
            "tasks"
        ][0]["status"]
        == "Выполнено"
    )


def test_queue_error_and_delete_during_processing(client, monkeypatch):
    m = create(client)
    ident = m["id"]
    assert client.post(f"/api/meetings/{ident}/jobs?kind=analysis").status_code == 200
    assert client.post(f"/api/meetings/{ident}/jobs?kind=analysis").status_code == 409

    def fail(*args):
        raise RuntimeError("private content should not be logged")

    monkeypatch.setattr(ai, "analyze", fail)
    worker.process_one()
    m = client.get(f"/api/meetings/{ident}").json()
    job = m["jobs"][0]
    assert job["state"] == "error" and "private content" not in job["error"]
    assert (
        client.post(f"/api/meetings/{ident}/jobs/{job['id']}/retry").status_code == 200
    )

    def delete_during(text, day, progress):
        client.delete("/api/meetings/" + ident)
        return fake_result(text)

    monkeypatch.setattr(ai, "analyze", delete_during)
    worker.process_one()
    assert client.get("/api/meetings/" + ident).status_code == 404
    assert client.get("/api/meetings").json() == []


def test_quotes_dates_and_chunk_coverage():
    text = "Анна подготовит отчёт завтра."
    r = ai.normalize(
        Result(
            summary="",
            tasks=[Task(text="Отчёт", quote=text, owner="Анна", due_raw="завтра")],
        ),
        text,
        "2026-10-01",
    )
    assert r.tasks[0].due_date == date(2026, 10, 2)
    assert r.tasks[0].owner_confirmed
    r.tasks[0].due_raw = "к пятнице"
    ai.normalize(r, text, "2026-10-01")
    assert not r.tasks[0].due_confirmed
    with pytest.raises(ValueError):
        ai.normalize(
            Result(summary="", decisions=[Decision(text="x", quote="нет")]),
            text,
            "2026-10-01",
        )
    source = "".join(chr(0x400 + i % 500) for i in range(200000))
    parts = list(ai.chunks(source))
    assert parts[0] == source[:6500] and parts[-1].endswith(source[-500:])
    assert len(parts) > 30


def test_external_origin_rejected(client):
    response = client.post(
        "/api/meetings", headers={"Origin": "https://example.com"}, json={}
    )
    assert response.status_code == 403


def test_prefixed_explicit_date():
    text = "Илья исправит ошибку до 03.10.2026."
    result = ai.normalize(
        Result(
            summary="",
            tasks=[Task(text="Исправить ошибку", quote=text, due_raw="до 03.10.2026")],
        ),
        text,
        "2026-10-01",
    )
    assert result.tasks[0].due_date == date(2026, 10, 3)


def test_queue_fifo_and_transcription_conflict(client, monkeypatch):
    first = create(client, "Первый текст")
    second = create(client, "Второй текст")
    seen = []

    def analyze(text, day, progress):
        seen.append(text)
        return Result(summary=text)

    monkeypatch.setattr(ai, "analyze", analyze)
    for m in (first, second):
        client.post(f"/api/meetings/{m['id']}/jobs?kind=analysis")
    worker.process_one()
    worker.process_one()
    assert seen == ["Первый текст", "Второй текст"]
    assert worker.process_one() is False


def test_replace_resets_status_and_is_atomic(client, monkeypatch):
    m = candidate(client, monkeypatch)
    ident = m["id"]
    r = m["results"]["candidate"]
    r["payload"]["tasks"][0].update(owner_confirmed=True, due_confirmed=True)
    client.put(
        f"/api/meetings/{ident}/results/candidate",
        json={"result": r["payload"], "version": 0},
    )
    current = client.post(f"/api/meetings/{ident}/accept?version=1").json()["results"][
        "current"
    ]
    current["payload"]["tasks"][0]["status"] = "Выполнено"
    client.put(
        f"/api/meetings/{ident}/results/current",
        json={"result": current["payload"], "version": current["version"]},
    )
    client.post(f"/api/meetings/{ident}/jobs?kind=analysis")
    worker.process_one()
    # Incomplete candidate cannot destroy the existing current result.
    assert client.post(f"/api/meetings/{ident}/accept?version=0").status_code == 400
    assert (
        client.get(f"/api/meetings/{ident}").json()["results"]["current"]["payload"][
            "tasks"
        ][0]["status"]
        == "Выполнено"
    )
    r = client.get(f"/api/meetings/{ident}").json()["results"]["candidate"]
    r["payload"]["tasks"][0].update(
        owner_confirmed=True, due_confirmed=True, status="Выполнено"
    )
    client.put(
        f"/api/meetings/{ident}/results/candidate",
        json={"result": r["payload"], "version": 0},
    )
    accepted = client.post(f"/api/meetings/{ident}/accept?version=1").json()
    assert "candidate" not in accepted["results"]
    assert (
        accepted["results"]["current"]["payload"]["tasks"][0]["status"] == "Не начато"
    )


def test_real_audio_decoder_and_limits(client, tmp_path):
    import wave
    from faster_whisper.audio import decode_audio
    from app.imports import audio_duration

    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    assert len(decode_audio(str(path))) == 16000
    assert audio_duration(path, ".wav") == 1
    m = create(client)
    response = client.post(
        f"/api/meetings/{m['id']}/files",
        files={"file": ("tone.wav", path.read_bytes())},
    )
    assert response.status_code == 200
    fid = response.json()["files"][0]["id"]
    assert (
        client.get(f"/api/meetings/{m['id']}/files/{fid}").content == path.read_bytes()
    )


def test_pdf_empty_and_valid(client):
    from pypdf import PdfWriter

    m = create(client)
    stream = io.BytesIO()
    pdf = PdfWriter()
    pdf.add_blank_page(width=200, height=200)
    pdf.write(stream)
    response = client.post(
        f"/api/meetings/{m['id']}/files",
        files={"file": ("empty.pdf", stream.getvalue())},
    )
    assert response.status_code == 400 and "текст" in response.text


def test_limits_do_not_replace_saved_text(client, monkeypatch):
    m = create(client, "Сохранённый текст")
    monkeypatch.setattr(main, "DOCUMENT_LIMIT", 10)
    response = client.post(
        f"/api/meetings/{m['id']}/files", files={"file": ("too-large.txt", b"x" * 11)}
    )
    assert response.status_code == 400
    assert client.get("/api/meetings/" + m["id"]).json()["text"] == "Сохранённый текст"
    assert client.get("/api/meetings/" + m["id"]).json()["files"] == []


def test_transcription_does_not_overwrite_new_draft(client, monkeypatch, tmp_path):
    import wave

    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    m = create(client)
    ident = m["id"]
    uploaded = client.post(
        f"/api/meetings/{ident}/files", files={"file": ("audio.wav", path.read_bytes())}
    ).json()
    fid = uploaded["files"][0]["id"]
    client.post(f"/api/meetings/{ident}/jobs?kind=transcription&file_id={fid}")

    def transcribe(path, progress):
        client.put(
            "/api/meetings/" + ident,
            json={
                "title": m["title"],
                "meeting_date": m["meeting_date"],
                "text": "Новые правки",
                "version": m["version"],
            },
        )
        return "Расшифровка"

    monkeypatch.setattr(ai, "transcribe", transcribe)
    worker.process_one()
    saved = client.get("/api/meetings/" + ident).json()
    assert saved["text"] == "Новые правки" and saved["jobs"][0]["state"] == "error"


def test_worker_recovers_interrupted_job_without_auto_retry(client, monkeypatch):
    import os
    import subprocess
    import sys
    import time

    m = create(client)
    ident = m["id"]
    client.post(f"/api/meetings/{ident}/jobs?kind=analysis")
    env = {**os.environ, "MEETINGS_DATA": str(db.DATA)}
    code = "from app import worker, ai; import time; ai.analyze=lambda *args: time.sleep(30); worker.run()"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (
                client.get(f"/api/meetings/{ident}").json()["jobs"][0]["state"]
                == "running"
            ):
                break
            time.sleep(0.05)
        assert (
            client.get(f"/api/meetings/{ident}").json()["jobs"][0]["state"] == "running"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    restarted = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (
                client.get(f"/api/meetings/{ident}").json()["jobs"][0]["state"]
                == "interrupted"
            ):
                break
            time.sleep(0.05)
        assert (
            client.get(f"/api/meetings/{ident}").json()["jobs"][0]["state"]
            == "interrupted"
        )
        # A second queue worker cannot acquire the same lock.
        duplicate = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, timeout=5
        )
        assert duplicate.returncode != 0 and "уже запущен" in duplicate.stderr.decode()
    finally:
        restarted.terminate()
        restarted.wait(timeout=5)


def test_text_size_boundary_and_unicode(client):
    allowed = "я" * 200_000
    response = client.post(
        "/api/meetings",
        json={"title": "Граница", "meeting_date": "2026-10-01", "text": allowed},
    )
    assert response.status_code == 201
    ident = response.json()["id"]
    rejected = client.put(
        "/api/meetings/" + ident,
        json={
            "title": "Граница",
            "meeting_date": "2026-10-01",
            "text": allowed + "я",
            "version": 0,
        },
    )
    assert rejected.status_code == 422
    assert client.get("/api/meetings/" + ident).json()["text"] == allowed


def test_document_exact_size_boundary(client, monkeypatch):
    monkeypatch.setattr(main, "DOCUMENT_LIMIT", 10)
    m = create(client, "")
    response = client.post(
        f"/api/meetings/{m['id']}/files", files={"file": ("exact.txt", b"0123456789")}
    )
    assert response.status_code == 200 and response.json()["text"] == "0123456789"
