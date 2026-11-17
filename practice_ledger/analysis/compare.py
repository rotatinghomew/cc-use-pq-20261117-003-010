from collections import defaultdict


def compare_scorecards(previous, current):
    """比较两份统计结果并保留离岗教师的变化记录。"""
    before = {row["teacher_id"]: row for row in previous.get("individual", [])}
    after = {row["teacher_id"]: row for row in current.get("individual", [])}
    teacher_ids = sorted(before.keys() | after.keys())
    teacher_changes = []
    for teacher_id in teacher_ids:
        old = before.get(teacher_id)
        new = after.get(teacher_id)
        old_minutes = old["total_minutes"] if old else 0
        new_minutes = new["total_minutes"] if new else 0
        old_department = old["department"] if old else None
        new_department = new["department"] if new else None
        if old_minutes != new_minutes or old_department != new_department or old is None or new is None:
            teacher_changes.append({
                "teacher_id": teacher_id,
                "name": (new or old)["name"],
                "previous_minutes": old_minutes,
                "current_minutes": new_minutes,
                "delta_minutes": new_minutes - old_minutes,
                "previous_department": old_department,
                "current_department": new_department,
                "status": "added" if old is None else "removed" if new is None else "changed",
            })
    before_departments = previous.get("department", {})
    after_departments = current.get("department", {})
    department_changes = {}
    for name in sorted(before_departments.keys() | after_departments.keys()):
        old = before_departments.get(name, {})
        new = after_departments.get(name, {})
        department_changes[name] = {
            field: new.get(field, 0) - old.get(field, 0)
            for field in ("teacher_count", "participants", "met_target", "total_minutes")
        }
    return {
        "previous_revision": previous.get("revision"),
        "current_revision": current.get("revision"),
        "period_changed": (previous.get("period_start"), previous.get("period_end")) !=
                          (current.get("period_start"), current.get("period_end")),
        "teacher_changes": teacher_changes,
        "department_changes": department_changes,
        "net_minutes": sum(item["delta_minutes"] for item in teacher_changes),
        "changed_teacher_count": len(teacher_changes),
    }


def source_concentration(records):
    totals = defaultdict(int)
    for record in records:
        totals[record["source"]] += record.get("credited_minutes", 0)
    combined = sum(totals.values())
    rows = []
    for source, minutes in sorted(totals.items()):
        rows.append({"source": source, "minutes": minutes,
                     "share": minutes / combined if combined else 0.0})
    return {"total_minutes": combined, "sources": rows}
