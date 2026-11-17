from datetime import datetime, timezone

from .rules import identifier, text


class Register:
    def __init__(self, store):
        self.store = store

    def upsert(self, records):
        if not isinstance(records, list) or not records:
            raise ValueError("teachers 必须为非空数组")
        clean = []
        seen = set()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError("教师记录必须为对象")
            teacher_id = identifier(item.get("teacher_id"), "teacher_id")
            if teacher_id in seen:
                raise ValueError("一次名册提交不能重复教师")
            seen.add(teacher_id)
            active = item.get("active", True)
            if type(active) is not bool:
                raise ValueError("active 必须为布尔值")
            clean.append((teacher_id, text(item.get("name"), "name", 80),
                          text(item.get("department"), "department", 80), int(active)))
        now = datetime.now(timezone.utc).isoformat()
        with self.store.transaction() as db:
            revision = self.store.bump(db)
            for teacher_id, name, department, active in clean:
                db.execute("""INSERT INTO teachers VALUES(?,?,?,?,?) ON CONFLICT(teacher_id) DO UPDATE SET
                    name=excluded.name,department=excluded.department,active=excluded.active,revision=excluded.revision""",
                           (teacher_id, name, department, active, revision))
            self.store.audit(db, "register", "teachers", {"ids": sorted(seen), "revision": revision}, now)
        return {"revision": revision, "count": len(clean)}

    def active(self, connection):
        return connection.execute("SELECT * FROM teachers WHERE active=1 ORDER BY teacher_id").fetchall()

    def get(self, teacher_id):
        from .rules import identifier
        key = identifier(teacher_id, "teacher_id")
        row = self.store.connection.execute("SELECT * FROM teachers WHERE teacher_id=?", (key,)).fetchone()
        return None if row is None else dict(row)

    def roster(self, *, department=None, include_inactive=False):
        clauses, arguments = [], []
        if department is not None:
            clauses.append("department=?")
            arguments.append(text(department, "department", 80))
        if not include_inactive:
            clauses.append("active=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.store.connection.execute("SELECT * FROM teachers" + where +
                                             " ORDER BY department,teacher_id", arguments)
        return [dict(row) for row in rows]

    def revision_for(self, teacher_id):
        current = self.get(teacher_id)
        return None if current is None else current["revision"]

    def department_counts(self, *, include_inactive=False):
        condition = "" if include_inactive else " WHERE active=1"
        rows = self.store.connection.execute("SELECT department,active,COUNT(*) AS count FROM teachers" +
            condition + " GROUP BY department,active ORDER BY department,active")
        grouped = {}
        for row in rows:
            record = grouped.setdefault(row["department"], {"active": 0, "inactive": 0})
            record["active" if row["active"] else "inactive"] = row["count"]
        return grouped
