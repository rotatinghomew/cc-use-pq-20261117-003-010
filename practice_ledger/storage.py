import json
import sqlite3
import threading
from contextlib import contextmanager


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES('revision',0);
CREATE TABLE IF NOT EXISTS teachers(
 teacher_id TEXT PRIMARY KEY,name TEXT NOT NULL,department TEXT NOT NULL,
 active INTEGER NOT NULL CHECK(active IN (0,1)),revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS batches(
 source TEXT NOT NULL,batch_id TEXT NOT NULL,digest TEXT NOT NULL,status TEXT NOT NULL,
 accepted INTEGER NOT NULL DEFAULT 0,rejected INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(source,batch_id));
CREATE TABLE IF NOT EXISTS rejects(
 source TEXT NOT NULL,batch_id TEXT NOT NULL,row_no INTEGER NOT NULL,payload TEXT NOT NULL,
 error TEXT NOT NULL,PRIMARY KEY(source,batch_id,row_no));
CREATE TABLE IF NOT EXISTS activities(
 activity_id TEXT PRIMARY KEY,source TEXT NOT NULL,batch_id TEXT NOT NULL,teacher_id TEXT NOT NULL,
 company TEXT NOT NULL,role TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,
 revision INTEGER NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)));
CREATE INDEX IF NOT EXISTS activities_teacher ON activities(teacher_id,active,starts_at);
CREATE TABLE IF NOT EXISTS decisions(
 decision_id INTEGER PRIMARY KEY AUTOINCREMENT,activity_id TEXT NOT NULL,revision INTEGER NOT NULL,decision TEXT NOT NULL,
 reviewer TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(activity_id) REFERENCES activities(activity_id));
CREATE TABLE IF NOT EXISTS previews(
 preview_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,period_start TEXT NOT NULL,
 period_end TEXT NOT NULL,threshold INTEGER NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases(
 release_id TEXT PRIMARY KEY,preview_id TEXT NOT NULL,revision INTEGER NOT NULL,
 body TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
 audit_id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,entity TEXT NOT NULL,
 detail TEXT NOT NULL,created_at TEXT NOT NULL);
"""


class Store:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self.lock = threading.RLock()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        self.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def revision(self, connection=None):
        db = connection or self.connection
        return db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def bump(self, connection):
        connection.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return self.revision(connection)

    def audit(self, connection, kind, entity, detail, created_at):
        connection.execute("INSERT INTO audit(kind,entity,detail,created_at) VALUES(?,?,?,?)",
                           (kind, entity, json.dumps(detail, ensure_ascii=False, sort_keys=True), created_at))

    def latest_release(self):
        row = self.connection.execute("SELECT * FROM releases ORDER BY created_at DESC,release_id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return dict(row)
