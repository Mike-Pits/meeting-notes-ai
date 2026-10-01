"""SQLite transactions and schema migration. Files belong to their meeting UUID."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DATA


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


@contextmanager
def connect():
    db = sqlite3.connect(DATA / "meetings.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    (DATA / "files").mkdir(exist_ok=True, mode=0o700)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            db.executescript("""
            CREATE TABLE meetings (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, meeting_date TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE files (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                name TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL,
                duration REAL, created_at TEXT NOT NULL);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
                source_text TEXT NOT NULL, source_date TEXT NOT NULL, source_version INTEGER NOT NULL,
                file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
                error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
            CREATE TABLE results (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, payload TEXT NOT NULL,
                source_text TEXT NOT NULL, source_date TEXT NOT NULL,
                model TEXT NOT NULL, created_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0, manual INTEGER NOT NULL DEFAULT 0,
                UNIQUE(meeting_id, kind));
            CREATE INDEX jobs_queue ON jobs(state, created_at);
            PRAGMA user_version=1;
            """)


def meeting(db, ident):
    row = db.execute(
        "SELECT * FROM meetings WHERE id=? AND deleted=0", (ident,)
    ).fetchone()
    if not row:
        raise LookupError("Встреча не найдена")
    return dict(row)


def details(ident):
    with connect() as db:
        item = meeting(db, ident)
        item["files"] = [
            dict(r)
            for r in db.execute(
                "SELECT id,name,kind,duration FROM files WHERE meeting_id=? ORDER BY created_at",
                (ident,),
            )
        ]
        item["jobs"] = [
            dict(r)
            for r in db.execute(
                "SELECT id,kind,state,stage,error,created_at,started_at,finished_at FROM jobs WHERE meeting_id=? ORDER BY created_at DESC",
                (ident,),
            )
        ]
        item["results"] = {}
        for row in db.execute("SELECT * FROM results WHERE meeting_id=?", (ident,)):
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            result["stale"] = (
                result["source_text"] != item["text"]
                or result["source_date"] != item["meeting_date"]
            )
            item["results"][row["kind"]] = result
        return item
