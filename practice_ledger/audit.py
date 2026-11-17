import json


class AuditTrail:
    def __init__(self, store):
        self.store = store

    def page(self, *, after=0, limit=100, kind=None, entity=None):
        if type(after) is not int or after < 0:
            raise ValueError("after必须为非负审计编号")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit必须为1至500")
        filters = ["audit_id > ?"]
        values = [after]
        if kind is not None:
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError("kind必须为非空文本")
            filters.append("kind=?")
            values.append(kind.strip())
        if entity is not None:
            if not isinstance(entity, str) or not entity.strip():
                raise ValueError("entity必须为非空文本")
            filters.append("entity=?")
            values.append(entity.strip())
        values.append(limit + 1)
        rows = self.store.connection.execute("SELECT * FROM audit WHERE " + " AND ".join(filters) +
                                             " ORDER BY audit_id LIMIT ?", values).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        events = [{"audit_id": row["audit_id"], "kind": row["kind"], "entity": row["entity"],
                   "detail": json.loads(row["detail"]), "created_at": row["created_at"]}
                  for row in selected]
        return {"events": events,
                "next_after": events[-1]["audit_id"] if events else after,
                "has_more": has_more}

    def entity_history(self, entity, *, limit=200):
        if not isinstance(entity, str) or not entity.strip():
            raise ValueError("entity必须为非空文本")
        result = self.page(after=0, limit=limit, entity=entity.strip())
        result["has_more"] = len(result["events"]) == limit
        return result["events"]

    def counts(self):
        rows = self.store.connection.execute("SELECT kind,COUNT(*) AS count FROM audit GROUP BY kind ORDER BY kind")
        return {row["kind"]: row["count"] for row in rows}

    def verify(self):
        previous = 0
        count = 0
        malformed = []
        for row in self.store.connection.execute("SELECT audit_id,detail FROM audit ORDER BY audit_id"):
            count += 1
            if row["audit_id"] <= previous:
                malformed.append({"audit_id": row["audit_id"], "problem": "non_increasing_id"})
            previous = row["audit_id"]
            try:
                detail = json.loads(row["detail"])
                if not isinstance(detail, dict):
                    raise ValueError("detail must be an object")
            except (ValueError, TypeError) as exc:
                malformed.append({"audit_id": row["audit_id"], "problem": str(exc)})
        return {"ok": not malformed, "event_count": count,
                "first_id": 1 if count else None, "last_id": previous if count else None,
                "malformed": malformed}
