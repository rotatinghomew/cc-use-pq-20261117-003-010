import hashlib
import json
from datetime import datetime, timezone

from .rules import identifier, interval, text


class Intake:
    fields = ("activity_id", "teacher_id", "company", "role", "starts_at", "ends_at")

    def __init__(self, store):
        self.store = store

    def normalize(self, row):
        if not isinstance(row, dict):
            raise ValueError("记录必须为JSON对象")
        activity_id = identifier(row.get("activity_id"), "activity_id")
        teacher_id = identifier(row.get("teacher_id"), "teacher_id")
        company = text(row.get("company"), "company", 120)
        role = text(row.get("role"), "role", 120)
        starts, ends = interval(row.get("starts_at"), row.get("ends_at"))
        return {"activity_id": activity_id, "teacher_id": teacher_id, "company": company,
                "role": role, "starts_at": starts.isoformat(), "ends_at": ends.isoformat()}

    def apply(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError("批次必须包含 rows 数组")
        source = identifier(payload.get("source"), "source")
        batch_id = identifier(payload.get("batch_id"), "batch_id")
        rows = payload["rows"]
        if not 1 <= len(rows) <= 1000:
            raise ValueError("批次须含1至1000条记录")
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()
        normalized, rejected, invalid_rows = self.inspect_rows(rows)
        now = datetime.now(timezone.utc).isoformat()
        with self.store.transaction() as db:
            prior = db.execute("SELECT * FROM batches WHERE source=? AND batch_id=?", (source, batch_id)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise ValueError("已处理批次内容不同；请使用新的batch_id")
                return self.result(db, source, batch_id, replayed=True)
            db.execute("INSERT INTO batches(source,batch_id,digest,status) VALUES(?,?,?,'applied')",
                       (source, batch_id, digest))
            revision = self.store.bump(db)
            for row_no, record in normalized:
                if row_no in invalid_rows:
                    continue
                old = db.execute("SELECT revision FROM activities WHERE activity_id=?",
                                 (record["activity_id"],)).fetchone()
                new_revision = old["revision"] + 1 if old else 1
                db.execute("""INSERT INTO activities VALUES(?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(activity_id) DO UPDATE SET source=excluded.source,batch_id=excluded.batch_id,
                    teacher_id=excluded.teacher_id,company=excluded.company,role=excluded.role,
                    starts_at=excluded.starts_at,ends_at=excluded.ends_at,revision=excluded.revision,active=1""",
                           (record["activity_id"], source, batch_id, record["teacher_id"], record["company"],
                            record["role"], record["starts_at"], record["ends_at"], new_revision))
            for row_no, row, error in rejected:
                db.execute("INSERT INTO rejects VALUES(?,?,?,?,?)",
                           (source, batch_id, row_no, json.dumps(row, ensure_ascii=False, sort_keys=True), error))
            db.execute("UPDATE batches SET accepted=?,rejected=? WHERE source=? AND batch_id=?",
                       (sum(row_no not in invalid_rows for row_no, _ in normalized), len(rejected), source, batch_id))
            self.store.audit(db, "intake", f"{source}/{batch_id}",
                             {"revision": revision, "activity_ids": [record["activity_id"] for row_no, record in normalized
                                                                        if row_no not in invalid_rows],
                              "accepted": sum(row_no not in invalid_rows for row_no, _ in normalized),
                              "rejected": len(rejected)}, now)
            return self.result(db, source, batch_id, replayed=False)

    def inspect_rows(self, rows):
        normalized, rejected, invalid_rows = [], [], set()
        seen_activities = set()
        for row_no, row in enumerate(rows, 1):
            try:
                clean = self.normalize(row)
                if clean["activity_id"] in seen_activities:
                    raise ValueError("同一批次重复提交activity_id")
                seen_activities.add(clean["activity_id"])
                normalized.append((row_no, clean))
            except (ValueError, TypeError) as exc:
                rejected.append((row_no, row, str(exc)))
                invalid_rows.add(row_no)
        known = {row[0]: row[1]["teacher_id"] for row in normalized}
        if known:
            placeholders = ",".join("?" for _ in known)
            roster = self.store.connection.execute(
                f"SELECT teacher_id,active FROM teachers WHERE teacher_id IN ({placeholders})",
                tuple(known.values())).fetchall()
            active = {row["teacher_id"]: row["active"] for row in roster}
            for row_no, teacher_id in known.items():
                if teacher_id not in active or not active[teacher_id]:
                    rejected.append((row_no, rows[row_no - 1], "教师不存在或已停用"))
                    invalid_rows.add(row_no)
        rejected.sort(key=lambda item: item[0])
        return normalized, rejected, invalid_rows

    def preview(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError("批次必须包含 rows 数组")
        source = identifier(payload.get("source"), "source")
        batch_id = identifier(payload.get("batch_id"), "batch_id")
        rows = payload["rows"]
        if not 1 <= len(rows) <= 1000:
            raise ValueError("批次须含1至1000条记录")
        normalized, rejected, invalid = self.inspect_rows(rows)
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()
        existing = self.store.connection.execute("SELECT digest FROM batches WHERE source=? AND batch_id=?",
                                                 (source, batch_id)).fetchone()
        replay = bool(existing and existing["digest"] == digest)
        conflict = bool(existing and existing["digest"] != digest)
        return {"source": source, "batch_id": batch_id,
                "valid_count": sum(row_no not in invalid for row_no, _ in normalized),
                "rejected_count": len(rejected), "replayed": replay, "identity_conflict": conflict,
                "rejected": [{"row_no": row_no, "error": error} for row_no, _, error in rejected]}

    def result(self, db, source, batch_id, replayed):
        batch = db.execute("SELECT * FROM batches WHERE source=? AND batch_id=?", (source, batch_id)).fetchone()
        rejects = db.execute("SELECT row_no,error FROM rejects WHERE source=? AND batch_id=? ORDER BY row_no",
                             (source, batch_id)).fetchall()
        activities = db.execute("SELECT activity_id,revision FROM activities WHERE source=? AND batch_id=? ORDER BY activity_id",
                                (source, batch_id)).fetchall()
        return {"source": source, "batch_id": batch_id, "accepted_count": batch["accepted"],
                "rejected_count": batch["rejected"], "replayed": replayed,
                "accepted": [{"activity_id": row["activity_id"], "revision": row["revision"]} for row in activities],
                "rejected": [dict(row) for row in rejects]}

    def batches(self, source=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit必须为1至1000")
        if source is None:
            rows = self.store.connection.execute("SELECT * FROM batches ORDER BY rowid DESC LIMIT ?", (limit,))
        else:
            rows = self.store.connection.execute("SELECT * FROM batches WHERE source=? ORDER BY rowid DESC LIMIT ?",
                                                 (identifier(source, "source"), limit))
        return [dict(row) for row in rows]

    def retryable_rejections(self, source, batch_id):
        rows = self.store.connection.execute("""SELECT row_no,payload,error FROM rejects
            WHERE source=? AND batch_id=? ORDER BY row_no""", (source, batch_id))
        return [{"row_no": row["row_no"], "payload": json.loads(row["payload"]),
                 "error": row["error"]} for row in rows]
