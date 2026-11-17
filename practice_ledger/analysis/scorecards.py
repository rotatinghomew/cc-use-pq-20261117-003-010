import hashlib
import json
import uuid
from datetime import datetime, timezone

from .intervals import daily_union
from .attribution import allocate_minutes
from .coverage import company_coverage, role_coverage
from .diagnostics import coverage_gaps, reconcile_department_totals, validate_scorecard
from .cohorts import monthly_teacher_activity
from .distribution import department_distribution


class Scorecards:
    def __init__(self, store):
        self.store = store

    def compute(self, connection, request, revision):
        start = request.get("period_start")
        end = request.get("period_end")
        threshold = request.get("target_minutes", 2400)
        privacy_k = request.get("privacy_k", 3)
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("period_start和period_end必须为YYYY-MM-DD")
        try:
            first = datetime.strptime(start, "%Y-%m-%d").date()
            last = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("统计日期必须为YYYY-MM-DD") from exc
        if first.isoformat() != start or last.isoformat() != end or last <= first:
            raise ValueError("统计日期范围无效")
        if type(threshold) is not int or not 1 <= threshold <= 200_000:
            raise ValueError("target_minutes必须为正整数")
        if type(privacy_k) is not int or not 2 <= privacy_k <= 100:
            raise ValueError("privacy_k必须为2至100")
        teachers = connection.execute("SELECT * FROM teachers WHERE active=1 ORDER BY teacher_id").fetchall()
        activities = connection.execute("""SELECT a.* FROM activities a JOIN decisions d
            ON d.decision_id=(SELECT MAX(latest.decision_id) FROM decisions latest
                WHERE latest.activity_id=a.activity_id AND latest.revision=a.revision)
                AND d.decision='approved'
            WHERE a.active=1 ORDER BY a.teacher_id,a.activity_id""").fetchall()
        by_teacher = {}
        for teacher in teachers:
            records = [dict(item) for item in activities if item["teacher_id"] == teacher["teacher_id"]]
            daily = daily_union(records, start, end)
            credited = allocate_minutes(records, start, end)
            total = sum(daily.values())
            by_teacher[teacher["teacher_id"]] = {
                "teacher_id": teacher["teacher_id"], "name": teacher["name"],
                "department": teacher["department"], "daily_minutes": daily,
                "credited_activity_minutes": credited,
                "total_minutes": total, "met_target": total >= threshold,
                "evidence": [{"activity_id": row["activity_id"], "revision": row["revision"],
                              "source": row["source"], "batch_id": row["batch_id"]} for row in records],
            }
        departments = {}
        groups = {}
        for teacher in teachers:
            groups.setdefault(teacher["department"], []).append(by_teacher[teacher["teacher_id"]])
        for department, members in sorted(groups.items()):
            departments[department] = {
                "teacher_count": len(members),
                "participants": sum(item["total_minutes"] > 0 for item in members),
                "met_target": sum(item["met_target"] for item in members),
                "total_minutes": sum(item["total_minutes"] for item in members),
            }
        body = {"revision": revision, "period_start": start, "period_end": end,
                "target_minutes": threshold, "privacy_k": privacy_k,
                "department": departments, "individual": list(by_teacher.values())}
        body["coverage"] = coverage_gaps(teachers, activities)
        body["company_coverage"] = company_coverage(activities, start, end,
                                                     roster_ids=by_teacher.keys())
        body["role_coverage"] = role_coverage(activities, start, end)
        body["reconciliation"] = reconcile_department_totals(departments, list(by_teacher.values()))
        body["distribution"] = department_distribution(list(by_teacher.values()), threshold)
        body["validation"] = validate_scorecard(body)
        body["public_department"] = self.public_view(departments, privacy_k)
        body["lineage"] = {"teacher_ids": sorted(by_teacher),
                            "activity_ids": sorted(item["activity_id"] for item in activities),
                            "decision_versions": sorted({(item["activity_id"], item["revision"])
                                                         for item in activities})}
        body["lineage_digest"] = hashlib.sha256(json.dumps(body["lineage"], sort_keys=True,
                                                            separators=(",", ":")).encode()).hexdigest()
        preview_id = uuid.uuid4().hex
        connection.execute("INSERT INTO previews VALUES(?,?,?,?,?,?,?)",
                           (preview_id, revision, start, end, privacy_k,
                            json.dumps(body, ensure_ascii=False, sort_keys=True),
                            datetime.now(timezone.utc).isoformat()))
        return {"preview_id": preview_id, "revision": revision, **body}

    def monthly(self, connection, request):
        start = request.get("period_start")
        end = request.get("period_end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("period_start和period_end必须为YYYY-MM-DD")
        teachers = connection.execute("SELECT teacher_id FROM teachers WHERE active=1").fetchall()
        activities = connection.execute("""SELECT a.* FROM activities a JOIN decisions d
            ON d.decision_id=(SELECT MAX(latest.decision_id) FROM decisions latest
                WHERE latest.activity_id=a.activity_id AND latest.revision=a.revision)
                AND d.decision='approved' WHERE a.active=1 ORDER BY a.teacher_id,a.activity_id""").fetchall()
        present = {row["teacher_id"] for row in teachers}
        eligible = [dict(row) for row in activities if row["teacher_id"] in present]
        return monthly_teacher_activity(eligible, start, end)

    @staticmethod
    def public_view(departments, privacy_k):
        rows = {name: dict(value) for name, value in departments.items()}
        hidden = {name for name, value in rows.items() if value["teacher_count"] < privacy_k}
        if len(hidden) == 1 and len(rows) > 1:
            smallest = min((name for name in rows if name not in hidden),
                           key=lambda name: rows[name]["teacher_count"])
            hidden.add(smallest)
        for name in hidden:
            rows[name] = {"suppressed": True}
        return rows
