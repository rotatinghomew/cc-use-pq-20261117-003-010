from collections import Counter
from datetime import datetime, timezone


class Quality:
    def __init__(self, store):
        self.store = store

    def summary(self, source=None, batch_id=None):
        clauses, parameters = [], []
        if source is not None:
            clauses.append("source=?")
            parameters.append(source)
        if batch_id is not None:
            clauses.append("batch_id=?")
            parameters.append(batch_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        batches = self.store.connection.execute("SELECT * FROM batches" + where +
                                                 " ORDER BY source,batch_id", parameters).fetchall()
        rejects = self.store.connection.execute("SELECT error FROM rejects" + where +
                                                " ORDER BY source,batch_id,row_no", parameters).fetchall()
        reasons = Counter(row["error"] for row in rejects)
        active = self.store.connection.execute("SELECT COUNT(*) FROM activities WHERE active=1").fetchone()[0]
        stale_decisions = self.store.connection.execute("""SELECT COUNT(*) FROM decisions d
            JOIN activities a ON a.activity_id=d.activity_id
            WHERE d.revision<>a.revision""").fetchone()[0]
        latest = self.store.latest_release()
        current_revision = self.store.revision()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "current_revision": current_revision,
            "batch_count": len(batches),
            "accepted_rows": sum(row["accepted"] for row in batches),
            "rejected_rows": sum(row["rejected"] for row in batches),
            "reject_reasons": dict(sorted(reasons.items())),
            "active_activities": active,
            "stale_decisions": stale_decisions,
            "latest_release": None if latest is None else {
                "release_id": latest["release_id"], "revision": latest["revision"],
                "current": latest["revision"] == current_revision,
            },
            "batches": [dict(row) for row in batches],
        }

    def rejected_rows(self, source, batch_id):
        return [dict(row) for row in self.store.connection.execute("""SELECT row_no,payload,error
            FROM rejects WHERE source=? AND batch_id=? ORDER BY row_no""", (source, batch_id))]

    def activity_sources(self, activity_id):
        row = self.store.connection.execute("""SELECT activity_id,source,batch_id,teacher_id,
            revision,active FROM activities WHERE activity_id=?""", (activity_id,)).fetchone()
        if row is None:
            return None
        history = self.store.connection.execute("""SELECT kind,detail,created_at FROM audit
            WHERE entity=? OR detail LIKE ? ORDER BY audit_id""", (activity_id, f"%{activity_id}%")).fetchall()
        return {"activity": dict(row), "history": [dict(item) for item in history]}

    def source_health(self):
        rows = self.store.connection.execute("""SELECT source,COUNT(*) AS batches,
            SUM(accepted) AS accepted,SUM(rejected) AS rejected
            FROM batches GROUP BY source ORDER BY source""").fetchall()
        result = []
        for row in rows:
            accepted = row["accepted"] or 0
            rejected = row["rejected"] or 0
            total = accepted + rejected
            result.append({"source": row["source"], "batches": row["batches"],
                           "accepted": accepted, "rejected": rejected,
                           "acceptance_rate": accepted / total if total else 0.0})
        return result

    def batch_readiness(self, source, batch_id, *, minimum_acceptance=0.95):
        if not isinstance(minimum_acceptance, (float, int)) or isinstance(minimum_acceptance, bool):
            raise ValueError("minimum_acceptance必须为0至1之间的数值")
        if not 0 <= minimum_acceptance <= 1:
            raise ValueError("minimum_acceptance必须为0至1之间的数值")
        batch = self.store.connection.execute("SELECT * FROM batches WHERE source=? AND batch_id=?",
                                             (source, batch_id)).fetchone()
        if batch is None:
            raise ValueError("批次不存在")
        total = batch["accepted"] + batch["rejected"]
        rate = batch["accepted"] / total if total else 0.0
        reasons = self.store.connection.execute("""SELECT error,COUNT(*) AS count FROM rejects
            WHERE source=? AND batch_id=? GROUP BY error ORDER BY error""", (source, batch_id)).fetchall()
        return {"source": source, "batch_id": batch_id,
                "accepted": batch["accepted"], "rejected": batch["rejected"],
                "acceptance_rate": rate, "minimum_acceptance": minimum_acceptance,
                "ready_for_analysis": rate >= minimum_acceptance and batch["accepted"] > 0,
                "rejection_reasons": [dict(row) for row in reasons]}

    def rejection_trends(self):
        rows = self.store.connection.execute("""SELECT b.source,COUNT(DISTINCT b.batch_id) AS batches,
            SUM(b.accepted) AS accepted,SUM(b.rejected) AS rejected,
            COUNT(r.row_no) AS quarantined FROM batches b LEFT JOIN rejects r
            ON r.source=b.source AND r.batch_id=b.batch_id
            GROUP BY b.source ORDER BY b.source""").fetchall()
        trends = []
        for row in rows:
            accepted, rejected = row["accepted"] or 0, row["rejected"] or 0
            total = accepted + rejected
            trends.append({"source": row["source"], "batch_count": row["batches"],
                           "accepted": accepted, "rejected": rejected,
                           "quarantined_rows": row["quarantined"],
                           "rejection_rate": rejected / total if total else 0.0})
        return trends

    def reconcile(self):
        database = self.store.connection
        checks = {
            "orphan_activities": """SELECT COUNT(*) FROM activities a LEFT JOIN teachers t
                ON t.teacher_id=a.teacher_id WHERE t.teacher_id IS NULL""",
            "orphan_decisions": """SELECT COUNT(*) FROM decisions d LEFT JOIN activities a
                ON a.activity_id=d.activity_id WHERE a.activity_id IS NULL""",
            "current_activity_without_batch": """SELECT COUNT(*) FROM activities a LEFT JOIN batches b
                ON b.source=a.source AND b.batch_id=a.batch_id WHERE b.batch_id IS NULL""",
            "decision_revision_ahead": """SELECT COUNT(*) FROM decisions d JOIN activities a
                ON a.activity_id=d.activity_id WHERE d.revision>a.revision""",
            "invalid_batch_counts": """SELECT COUNT(*) FROM batches
                WHERE accepted<0 OR rejected<0 OR accepted+rejected<1""",
            "reject_count_mismatch": """SELECT COUNT(*) FROM batches b WHERE rejected <>
                (SELECT COUNT(*) FROM rejects r WHERE r.source=b.source AND r.batch_id=b.batch_id)""",
            "invalid_decision_values": """SELECT COUNT(*) FROM decisions
                WHERE decision NOT IN ('approved','rejected','needs_info')""",
            "release_revision_ahead": """SELECT COUNT(*) FROM releases r
                WHERE r.revision > (SELECT value FROM meta WHERE key='revision')""",
        }
        results = {name: database.execute(query).fetchone()[0] for name, query in checks.items()}
        return {"ok": all(value == 0 for value in results.values()), "checks": results}
