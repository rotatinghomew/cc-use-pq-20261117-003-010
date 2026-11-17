import sqlite3
from pathlib import Path


class Maintenance:
    required_tables = {"meta", "teachers", "batches", "rejects", "activities",
                       "decisions", "previews", "releases", "audit"}

    def __init__(self, store):
        self.store = store

    def integrity(self):
        database = self.store.connection
        integrity_rows = database.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = database.execute("PRAGMA foreign_key_check").fetchall()
        tables = {row["name"] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(self.required_tables - tables)
        failures = [row[0] for row in integrity_rows if row[0] != "ok"]
        return {"ok": not failures and not foreign_keys and not missing,
                "integrity": [row[0] for row in integrity_rows],
                "foreign_key_errors": [tuple(row) for row in foreign_keys],
                "missing_tables": missing,
                "revision": self.store.revision()}

    def checkpoint(self, mode="PASSIVE"):
        if mode not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("checkpoint mode无效")
        row = self.store.connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return {"busy": row[0], "log_pages": row[1], "checkpointed_pages": row[2],
                "mode": mode, "revision": self.store.revision()}

    def journal_status(self):
        mode = self.store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        page_size = self.store.connection.execute("PRAGMA page_size").fetchone()[0]
        page_count = self.store.connection.execute("PRAGMA page_count").fetchone()[0]
        foreign_keys = self.store.connection.execute("PRAGMA foreign_keys").fetchone()[0]
        return {"journal_mode": mode, "page_size": page_size,
                "page_count": page_count, "database_bytes": page_size * page_count,
                "foreign_keys_enabled": bool(foreign_keys),
                "revision": self.store.revision()}

    def backup(self, destination):
        target = Path(destination)
        if target.exists():
            raise FileExistsError("备份目标已存在，不覆盖既有文件")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(target)
        try:
            self.store.connection.backup(backup)
            result = backup.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError("备份完整性检查失败：" + result)
            count = backup.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
            return {"path": str(target), "integrity": result,
                    "audit_events": count, "revision": backup.execute(
                        "SELECT value FROM meta WHERE key='revision'").fetchone()[0]}
        except BaseException:
            backup.close()
            target.unlink(missing_ok=True)
            raise
        finally:
            try:
                backup.close()
            except sqlite3.Error:
                pass

    @classmethod
    def restore_copy(cls, source, destination):
        origin = Path(source)
        target = Path(destination)
        if not origin.is_file():
            raise FileNotFoundError("备份文件不存在")
        if target.exists():
            raise FileExistsError("恢复目标已存在，不覆盖既有数据库")
        assessment = cls.inspect_file(origin)
        if not assessment["ok"]:
            raise sqlite3.DatabaseError("备份未通过完整性检查")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_db = sqlite3.connect(f"file:{origin.resolve()}?mode=ro", uri=True)
        target_db = sqlite3.connect(target)
        try:
            source_db.backup(target_db)
            checked = cls.inspect_file(target)
            if not checked["ok"] or checked["revision"] != assessment["revision"]:
                raise sqlite3.DatabaseError("恢复副本与来源备份不一致")
            return {"path": str(target), "revision": checked["revision"], "ok": True}
        except BaseException:
            target_db.close()
            target.unlink(missing_ok=True)
            raise
        finally:
            source_db.close()
            try:
                target_db.close()
            except sqlite3.Error:
                pass

    @classmethod
    def inspect_file(cls, path):
        database = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        database.row_factory = sqlite3.Row
        try:
            integrity = database.execute("PRAGMA integrity_check").fetchall()
            tables = {row["name"] for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            foreign_keys = database.execute("PRAGMA foreign_key_check").fetchall()
            missing = sorted(cls.required_tables - tables)
            failures = [row[0] for row in integrity if row[0] != "ok"]
            revision = database.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
            return {"ok": not failures and not foreign_keys and not missing and revision is not None,
                    "integrity": [row[0] for row in integrity],
                    "foreign_key_errors": [tuple(row) for row in foreign_keys],
                    "missing_tables": missing,
                    "revision": revision[0] if revision else None}
        finally:
            database.close()
