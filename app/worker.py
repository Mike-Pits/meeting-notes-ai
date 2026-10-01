"""Single durable queue consumer, protected by an OS lock across processes."""

import fcntl
import json
import time
from . import db, ai
from .config import DATA, MODEL


def run():
    db.initialize()
    with open(DATA / "worker.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Обработчик очереди уже запущен.")
        with db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='interrupted',stage='Обработка прервана. Повторите по кнопке.' WHERE state='running'"
            )
        while True:
            if not process_one():
                time.sleep(1)


def process_one():
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT j.* FROM jobs j JOIN meetings m ON m.id=j.meeting_id WHERE j.state='queued' AND m.deleted=0 ORDER BY j.created_at,j.rowid LIMIT 1"
        ).fetchone()
        if not row:
            return False
        job = dict(row)
        conn.execute(
            "UPDATE jobs SET state='running',started_at=?,stage='Подготовка' WHERE id=?",
            (db.now(), job["id"]),
        )

    def progress(stage):
        with db.connect() as conn:
            db.meeting(conn, job["meeting_id"])
            conn.execute("UPDATE jobs SET stage=? WHERE id=?", (stage, job["id"]))

    try:
        if job["kind"] == "analysis":
            result = ai.analyze(job["source_text"], job["source_date"], progress)
            with db.connect() as conn:
                db.meeting(conn, job["meeting_id"])
                conn.execute(
                    "DELETE FROM results WHERE meeting_id=? AND kind='candidate'",
                    (job["meeting_id"],),
                )
                conn.execute(
                    "INSERT INTO results(id,meeting_id,kind,payload,source_text,source_date,model,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        db.uid(),
                        job["meeting_id"],
                        "candidate",
                        result.model_dump_json(),
                        job["source_text"],
                        job["source_date"],
                        MODEL + "/prompt-" + ai.PROMPT_VERSION,
                        db.now(),
                    ),
                )
        else:
            with db.connect() as conn:
                file = conn.execute(
                    "SELECT * FROM files WHERE id=?", (job["file_id"],)
                ).fetchone()
                if not file:
                    raise LookupError("Аудио удалено")
            text = ai.transcribe(DATA / file["path"], progress)
            with db.connect() as conn:
                current = db.meeting(conn, job["meeting_id"])
                if current["version"] != job["source_version"]:
                    raise ValueError(
                        "Текст изменился во время расшифровки. Ваши правки сохранены; повторите расшифровку при необходимости."
                    )
                conn.execute(
                    "UPDATE meetings SET text=?,version=version+1,updated_at=? WHERE id=?",
                    (text, db.now(), job["meeting_id"]),
                )
        with db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='done',stage=?,finished_at=? WHERE id=?",
                (
                    (
                        "Проверьте расшифровку"
                        if job["kind"] == "transcription"
                        else "Проверьте новый результат"
                    ),
                    db.now(),
                    job["id"],
                ),
            )
    except Exception as error:
        # Never persist provider response bodies or source material in logs.
        if isinstance(error, (ValueError, LookupError)):
            message = str(error)[:300]
        else:
            message = (
                "Локальная обработка недоступна. Проверьте модели и память GPU, затем повторите. Тип: "
                + type(error).__name__
            )
        with db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='error',stage='Ошибка',error=?,finished_at=? WHERE id=?",
                (message, db.now(), job["id"]),
            )
    return True


if __name__ == "__main__":
    run()
