from urllib.parse import parse_qs, urlsplit

from .rules import identifier, instant, text


class ActivitySearch:
    def __init__(self, store):
        self.store = store

    def search(self, *, teacher_id=None, source=None, company=None, department=None,
               decision=None, starts_after=None, ends_before=None, active_only=True,
               after_id="", limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit必须为1至500")
        if type(active_only) is not bool:
            raise ValueError("active_only必须为布尔值")
        conditions, values = [], []
        if active_only:
            conditions.append("a.active=1")
        for value, field, column in (
            (teacher_id, "teacher_id", "a.teacher_id"),
            (source, "source", "a.source"),
            (company, "company", "a.company"),
        ):
            if value is not None:
                conditions.append(column + "=?")
                values.append(identifier(value, field) if field != "company" else text(value, field, 120))
        if department is not None:
            conditions.append("t.department=?")
            values.append(text(department, "department", 80))
        if starts_after is not None:
            conditions.append("a.ends_at>?" )
            values.append(instant(starts_after, "starts_after").isoformat())
        if ends_before is not None:
            conditions.append("a.starts_at<?")
            values.append(instant(ends_before, "ends_before").isoformat())
        if decision is not None:
            if decision not in {"approved", "rejected", "needs_info", "pending"}:
                raise ValueError("decision筛选值无效")
            if decision == "pending":
                conditions.append("d.decision_id IS NULL")
            else:
                conditions.append("d.decision=?")
                values.append(decision)
        if after_id:
            conditions.append("a.activity_id>?" )
            values.append(identifier(after_id, "after_id"))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = """SELECT a.activity_id,a.source,a.batch_id,a.teacher_id,t.name,t.department,
            a.company,a.role,a.starts_at,a.ends_at,a.revision,a.active,d.decision,
            d.reviewer,d.note,d.created_at AS reviewed_at FROM activities a
            LEFT JOIN teachers t ON t.teacher_id=a.teacher_id
            LEFT JOIN decisions d ON d.decision_id=(SELECT MAX(latest.decision_id) FROM decisions latest
                WHERE latest.activity_id=a.activity_id AND latest.revision=a.revision)"""
        rows = self.store.connection.execute(query + where +
            " ORDER BY a.activity_id LIMIT ?", (*values, limit + 1)).fetchall()
        more = len(rows) > limit
        items = [dict(row) for row in rows[:limit]]
        return {"items": items,
                "next_after": items[-1]["activity_id"] if items else after_id,
                "has_more": more}

    def facets(self, *, department=None):
        conditions, values = ["a.active=1"], []
        if department is not None:
            conditions.append("t.department=?")
            values.append(text(department, "department", 80))
        where = " AND ".join(conditions)
        rows = self.store.connection.execute("""SELECT a.source,a.company,a.role,
            COALESCE(d.decision,'pending') AS decision,COUNT(*) AS count
            FROM activities a LEFT JOIN teachers t ON t.teacher_id=a.teacher_id
            LEFT JOIN decisions d ON d.decision_id=(SELECT MAX(latest.decision_id) FROM decisions latest
                WHERE latest.activity_id=a.activity_id AND latest.revision=a.revision)
            WHERE """ + where + " GROUP BY a.source,a.company,a.role,decision ORDER BY a.source,a.company,a.role,decision",
                                                       values).fetchall()
        return [dict(row) for row in rows]

    @classmethod
    def from_url(cls, service, url):
        params = parse_qs(urlsplit(url).query, keep_blank_values=True, strict_parsing=False)
        allowed = {"teacher_id", "source", "company", "department", "decision",
                   "starts_after", "ends_before", "after_id", "limit"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("未知筛选字段：" + ",".join(unknown))
        values = {}
        for key, items in params.items():
            if len(items) != 1:
                raise ValueError(f"{key}只能指定一次")
            values[key] = items[0]
        if "limit" in values:
            try:
                values["limit"] = int(values["limit"])
            except ValueError as exc:
                raise ValueError("limit必须为整数") from exc
        return service.search(**values)
