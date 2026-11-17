import json
from datetime import datetime, timezone

from .rules import identifier, text


class Assurance:
    decisions = {"approved", "rejected", "needs_info"}

    def __init__(self, store):
        self.store = store

    def decide(self, request):
        activity_id = identifier(request.get("activity_id"), "activity_id")
        expected = request.get("expected_revision")
        if type(expected) is not int or expected < 1:
            raise ValueError("expected_revision 必须为正整数")
        decision = request.get("decision")
        if decision not in self.decisions:
            raise ValueError("decision 必须为 approved、rejected 或 needs_info")
        reviewer = text(request.get("reviewer"), "reviewer", 80)
        note = text(request.get("note", decision), "note", 500)
        now = datetime.now(timezone.utc).isoformat()
        with self.store.transaction() as db:
            activity = db.execute("SELECT * FROM activities WHERE activity_id=? AND active=1",
                                  (activity_id,)).fetchone()
            if activity is None:
                raise ValueError("活动不存在或已撤销")
            if activity["revision"] != expected:
                raise ValueError("活动版本已变化，请重新核对凭证")
            if decision == "approved":
                teacher = db.execute("SELECT active FROM teachers WHERE teacher_id=?",
                                     (activity["teacher_id"],)).fetchone()
                if teacher is None or not teacher["active"]:
                    raise ValueError("非在册教师的活动不能通过审核")
            db.execute("INSERT INTO decisions(activity_id,revision,decision,reviewer,note,created_at) VALUES(?,?,?,?,?,?)",
                       (activity_id, expected, decision, reviewer, note, now))
            revision = self.store.bump(db)
            self.store.audit(db, "decision", activity_id,
                             {"activity_revision": expected, "decision": decision,
                              "reviewer": reviewer, "revision": revision}, now)
        return {"activity_id": activity_id, "activity_revision": expected,
                "decision": decision, "system_revision": revision}

    def current(self, connection, activity_id, revision):
        return connection.execute("""SELECT * FROM decisions
            WHERE activity_id=? AND revision=? ORDER BY decision_id DESC LIMIT 1""",
                                  (activity_id, revision)).fetchone()

    def history(self, activity_id):
        identifier(activity_id, "activity_id")
        rows = self.store.connection.execute("SELECT * FROM decisions WHERE activity_id=? ORDER BY decision_id",
                                             (activity_id,)).fetchall()
        return [dict(row) for row in rows]
