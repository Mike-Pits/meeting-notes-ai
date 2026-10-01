import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import db, imports, exports
from .config import DATA, ROOT, AUDIO_LIMIT, DOCUMENT_LIMIT
from .schemas import MeetingInput, ResultInput


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    yield


app = FastAPI(title="Итоги встреч", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


@app.middleware("http")
async def local_requests(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse(
                {"detail": "Запрос разрешён только из локального приложения."},
                status_code=403,
            )
        if request.headers.get("x-meetings-client") != "local":
            return JSONResponse(
                {"detail": "Отсутствует заголовок локального приложения."},
                status_code=403,
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


@app.exception_handler(LookupError)
async def not_found(request, error):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(ValueError)
async def invalid(request, error):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.get("/")
def index():
    return FileResponse(ROOT / "app/static/index.html")


@app.get("/api/meetings")
def list_meetings():
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT m.id,m.title,m.meeting_date,m.created_at,
          (SELECT stage FROM jobs j WHERE j.meeting_id=m.id ORDER BY created_at DESC,rowid DESC LIMIT 1) AS stage,
          (SELECT state FROM jobs j WHERE j.meeting_id=m.id ORDER BY created_at DESC,rowid DESC LIMIT 1) AS job_state,
          EXISTS(SELECT 1 FROM results r WHERE r.meeting_id=m.id AND r.kind='candidate') AS has_candidate,
          EXISTS(SELECT 1 FROM results r WHERE r.meeting_id=m.id AND r.kind='current') AS has_result
          FROM meetings m WHERE deleted=0 ORDER BY created_at DESC,rowid DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/meetings", status_code=201)
def create(body: MeetingInput):
    if not body.title.strip():
        raise ValueError("Введите название встречи.")
    ident = db.uid()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO meetings(id,title,meeting_date,text,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (
                ident,
                body.title.strip(),
                body.meeting_date.isoformat(),
                body.text,
                db.now(),
                db.now(),
            ),
        )
    return db.details(ident)


@app.get("/api/meetings/{ident}")
def get(ident: str):
    return db.details(ident)


@app.put("/api/meetings/{ident}")
def update(ident: str, body: MeetingInput):
    if not body.title.strip():
        raise ValueError("Введите название встречи.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = db.meeting(conn, ident)
        if current["version"] != body.version:
            raise HTTPException(
                409,
                "Данные изменились в другом окне. Обновите встречу, предварительно скопировав свои правки.",
            )
        conn.execute(
            "UPDATE meetings SET title=?,meeting_date=?,text=?,version=version+1,updated_at=? WHERE id=?",
            (
                body.title.strip(),
                body.meeting_date.isoformat(),
                body.text,
                db.now(),
                ident,
            ),
        )
    return db.details(ident)


@app.post("/api/meetings/{ident}/files")
def upload(ident: str, file: UploadFile = File(...), recording: bool = False):
    with db.connect() as conn:
        current = db.meeting(conn, ident)
    suffix = Path(file.filename or "").suffix.lower()
    audio = suffix in (".mp3", ".wav", ".m4a") or (recording and suffix == ".webm")
    if not audio and suffix not in (".txt", ".docx", ".pdf"):
        raise ValueError("Поддерживаются TXT, DOCX, PDF, MP3, WAV, M4A.")
    folder = DATA / "files" / ident
    folder.mkdir(exist_ok=True, mode=0o700)
    fid = db.uid()
    path = folder / (fid + suffix)
    total = 0
    try:
        with path.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > (AUDIO_LIMIT if audio else DOCUMENT_LIMIT):
                    raise ValueError(
                        "Превышен размер: аудио до 500 МБ, документ до 50 МБ."
                    )
                out.write(chunk)
        if not total:
            raise ValueError("Файл пуст.")
        if audio:
            duration = imports.audio_duration(path, suffix)
        else:
            try:
                text = imports.document_text(path, suffix)
            except ValueError:
                raise
            except Exception as error:
                raise ValueError(
                    "Не удалось прочитать документ. Проверьте формат и кодировку UTF-8."
                ) from error
            duration = None
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            latest = db.meeting(conn, ident)
            if latest["version"] != current["version"]:
                raise HTTPException(
                    409, "Текст изменился во время загрузки. Загрузите файл повторно."
                )
            conn.execute(
                "INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                (
                    fid,
                    ident,
                    Path(file.filename or "Запись").name,
                    str(path.relative_to(DATA)),
                    "audio" if audio else "document",
                    duration,
                    db.now(),
                ),
            )
            if not audio:
                conn.execute(
                    "UPDATE meetings SET text=?,version=version+1,updated_at=? WHERE id=?",
                    (text, db.now(), ident),
                )
        return db.details(ident)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@app.get("/api/meetings/{ident}/files/{fid}")
def audio_file(ident: str, fid: str):
    with db.connect() as conn:
        db.meeting(conn, ident)
        row = conn.execute(
            "SELECT * FROM files WHERE id=? AND meeting_id=?", (fid, ident)
        ).fetchone()
        if not row:
            raise LookupError("Файл не найден")
    return FileResponse(DATA / row["path"])


@app.post("/api/meetings/{ident}/jobs")
def enqueue(ident: str, kind: str, file_id: str | None = None):
    if kind not in ("analysis", "transcription"):
        raise ValueError("Неизвестный тип обработки.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = db.meeting(conn, ident)
        if conn.execute(
            "SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('queued','running')",
            (ident,),
        ).fetchone():
            raise HTTPException(409, "Для этой встречи уже есть задание в очереди.")
        if kind == "analysis":
            if not current["text"].strip():
                raise ValueError("Сначала введите или проверьте текст встречи.")
            if conn.execute(
                "SELECT 1 FROM results WHERE meeting_id=? AND kind='candidate'",
                (ident,),
            ).fetchone():
                raise HTTPException(
                    409, "Сначала примите или отклоните предыдущий новый результат."
                )
        else:
            if not conn.execute(
                "SELECT 1 FROM files WHERE id=? AND meeting_id=? AND kind='audio'",
                (file_id, ident),
            ).fetchone():
                raise ValueError("Выберите сохранённое аудио.")
        conn.execute(
            "INSERT INTO jobs(id,meeting_id,kind,state,stage,source_text,source_date,source_version,file_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                db.uid(),
                ident,
                kind,
                "queued",
                "В очереди",
                current["text"],
                current["meeting_date"],
                current["version"],
                file_id,
                db.now(),
            ),
        )
    return db.details(ident)


@app.post("/api/meetings/{ident}/jobs/{jid}/retry")
def retry(ident: str, jid: str):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id=? AND meeting_id=? AND state IN ('error','interrupted')",
            (jid, ident),
        ).fetchone()
        if not row:
            raise ValueError("Это задание нельзя повторить.")
    return enqueue(ident, row["kind"], row["file_id"])


@app.put("/api/meetings/{ident}/results/{kind}")
def save_result(ident: str, kind: str, body: ResultInput):
    if kind not in ("candidate", "current"):
        raise ValueError("Неизвестный результат.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.meeting(conn, ident)
        row = conn.execute(
            "SELECT * FROM results WHERE meeting_id=? AND kind=?", (ident, kind)
        ).fetchone()
        if not row:
            raise LookupError("Результат не найден")
        if row["version"] != body.version:
            raise HTTPException(
                409, "Результат изменился. Скопируйте правки и обновите страницу."
            )
        old = json.loads(row["payload"])
        result = body.result
        for item in [*result.decisions, *result.tasks]:
            if item.quote not in row["source_text"]:
                raise ValueError(
                    "Цитата должна присутствовать в исходной редакции текста."
                )
        for index, task in enumerate(result.tasks):
            previous = old["tasks"][index] if index < len(old["tasks"]) else None
            if not previous or task.model_dump(mode="json") != previous:
                task.manual = True
            if kind == "candidate":
                task.status = "Не начато"
        conn.execute(
            "UPDATE results SET payload=?,version=version+1,manual=1 WHERE id=?",
            (result.model_dump_json(), row["id"]),
        )
    return db.details(ident)


@app.post("/api/meetings/{ident}/accept")
def accept(ident: str, version: int):
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.meeting(conn, ident)
        row = conn.execute(
            "SELECT * FROM results WHERE meeting_id=? AND kind='candidate'", (ident,)
        ).fetchone()
        if not row:
            raise LookupError("Нет нового результата")
        if row["version"] != version:
            raise HTTPException(409, "Новый результат изменился. Обновите страницу.")
        result = json.loads(row["payload"])
        for task in result["tasks"]:
            if not task["owner_confirmed"] or not task["due_confirmed"]:
                raise ValueError(
                    "Ответьте на все уточнения либо отметьте «Не указано»."
                )
            task["status"] = "Не начато"
        conn.execute(
            "DELETE FROM results WHERE meeting_id=? AND kind='current'", (ident,)
        )
        conn.execute(
            "UPDATE results SET kind='current',payload=?,version=version+1 WHERE id=?",
            (json.dumps(result, ensure_ascii=False), row["id"]),
        )
    return db.details(ident)


@app.delete("/api/meetings/{ident}/candidate")
def discard(ident: str):
    with db.connect() as conn:
        db.meeting(conn, ident)
        conn.execute(
            "DELETE FROM results WHERE meeting_id=? AND kind='candidate'", (ident,)
        )
    return db.details(ident)


@app.delete("/api/meetings/{ident}")
def delete(ident: str):
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM meetings WHERE id=?", (ident,)).fetchone()
        if not row:
            raise LookupError("Встреча не найдена")
        conn.execute("UPDATE meetings SET deleted=1 WHERE id=?", (ident,))
    folder = DATA / "files" / ident
    try:
        if folder.exists():
            shutil.rmtree(folder)
    except OSError as error:
        # Keep a visible tombstone so deletion can be retried.
        with db.connect() as conn:
            conn.execute("UPDATE meetings SET deleted=0 WHERE id=?", (ident,))
        raise ValueError("Не все файлы удалены. Повторите удаление встречи.") from error
    with db.connect() as conn:
        conn.execute("DELETE FROM meetings WHERE id=?", (ident,))
    return {"deleted": True}


@app.get("/api/meetings/{ident}/export/{format}")
def export(ident: str, format: str):
    meeting = db.details(ident)
    current = meeting["results"].get("current")
    if not current:
        raise ValueError("Сначала подтвердите результат.")
    if format == "md":
        return Response(
            exports.markdown(meeting, current["payload"]),
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="meeting.md"'},
        )
    if format == "docx":
        return Response(
            exports.docx_bytes(meeting, current["payload"]),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="meeting.docx"'},
        )
    raise ValueError("Неизвестный формат экспорта.")


app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
