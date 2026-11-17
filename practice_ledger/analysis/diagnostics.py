from collections import Counter, defaultdict
from datetime import datetime


def inspect_activity_rows(records):
    """检查重复和时间重叠并返回可处理的问题，不改写凭证。"""
    records = list(records)
    findings = []
    identifiers = Counter(row.get("activity_id") for row in records)
    for activity_id, count in sorted(identifiers.items(), key=lambda item: str(item[0])):
        if count > 1:
            findings.append({"kind": "duplicate_activity_id", "activity_id": activity_id,
                             "count": count, "severity": "error"})
    by_teacher = defaultdict(list)
    for row in records:
        try:
            left = datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00"))
            right = datetime.fromisoformat(row["ends_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            findings.append({"kind": "invalid_timestamp", "activity_id": row.get("activity_id"),
                             "severity": "error"})
            continue
        if left.utcoffset() is None or right.utcoffset() is None or right <= left:
            findings.append({"kind": "invalid_interval", "activity_id": row.get("activity_id"),
                             "severity": "error"})
            continue
        by_teacher[row.get("teacher_id")].append((left, right, row))
    for teacher_id, intervals in sorted(by_teacher.items(), key=lambda item: str(item[0])):
        intervals.sort(key=lambda item: (item[0], item[1], str(item[2].get("activity_id"))))
        furthest_end, furthest_row = None, None
        for left, right, row in intervals:
            if furthest_end is not None and left < furthest_end:
                findings.append({"kind": "overlap", "teacher_id": teacher_id,
                                 "activity_id": row.get("activity_id"),
                                 "with_activity_id": furthest_row.get("activity_id"),
                                 "overlap_seconds": int((min(right, furthest_end) - left).total_seconds()),
                                 "severity": "warning"})
            if furthest_end is None or right > furthest_end:
                furthest_end, furthest_row = right, row
    return sorted(findings, key=lambda item: (item.get("teacher_id", ""),
                                               item.get("activity_id", ""), item["kind"]))


def rejection_profile(rows):
    counts = Counter(row.get("error", "unknown") for row in rows)
    total = len(rows)
    return {"total": total,
            "reasons": [{"reason": reason, "count": count,
                         "share": count / total if total else 0.0}
                        for reason, count in sorted(counts.items())]}


def coverage_gaps(roster, activities):
    active = {row["teacher_id"] for row in roster if row["active"]}
    represented = {row["teacher_id"] for row in activities}
    missing = sorted(active - represented)
    outside = sorted(represented - active)
    return {"active_teacher_count": len(active),
            "represented_teacher_count": len(active & represented),
            "without_activity": missing, "outside_active_roster": outside,
            "coverage_rate": len(active & represented) / len(active) if active else 0.0}


def reconcile_department_totals(departments, individuals):
    derived = defaultdict(lambda: {"teacher_count": 0, "participants": 0,
                                   "met_target": 0, "total_minutes": 0})
    for person in individuals:
        row = derived[person["department"]]
        row["teacher_count"] += 1
        row["participants"] += person["total_minutes"] > 0
        row["met_target"] += bool(person["met_target"])
        row["total_minutes"] += person["total_minutes"]
    discrepancies = []
    for department in sorted(derived.keys() | departments.keys()):
        actual = departments.get(department, {})
        expected = derived.get(department, {})
        for metric in ("teacher_count", "participants", "met_target", "total_minutes"):
            if actual.get(metric, 0) != expected.get(metric, 0):
                discrepancies.append({"department": department, "metric": metric,
                                      "reported": actual.get(metric, 0),
                                      "derived": expected.get(metric, 0)})
    return {"ok": not discrepancies, "discrepancies": discrepancies}


def validate_scorecard(body):
    problems = []
    if type(body.get("revision")) is not int or body["revision"] < 0:
        problems.append({"check": "revision", "expected": "non-negative integer"})
    if body.get("period_end", "") <= body.get("period_start", ""):
        problems.append({"check": "period_order", "expected": "end after start"})
    people = body.get("individual", [])
    teacher_ids = [item.get("teacher_id") for item in people]
    if len(teacher_ids) != len(set(teacher_ids)):
        problems.append({"check": "unique_teacher", "expected": "unique identifiers"})
    credited_by_day = defaultdict(int)
    for person in people:
        for day, allocations in person.get("credited_activity_minutes", {}).items():
            credited_by_day[(person.get("teacher_id"), day)] += sum(allocations.values())
    for (teacher_id, day), minutes in credited_by_day.items():
        if minutes > 480:
            problems.append({"check": "allocated_daily_cap", "teacher_id": teacher_id,
                             "day": day, "minutes": minutes})
    for person in people:
        if not isinstance(person.get("department"), str) or not person["department"]:
            problems.append({"check": "teacher_department", "teacher_id": person.get("teacher_id")})
        daily = person.get("daily_minutes", {})
        total = person.get("total_minutes")
        if any(type(value) is not int or not 0 <= value <= 480 for value in daily.values()):
            problems.append({"check": "daily_cap", "teacher_id": person.get("teacher_id")})
        if sum(daily.values()) != total:
            problems.append({"check": "daily_total", "teacher_id": person.get("teacher_id")})
        credited = person.get("credited_activity_minutes", {})
        allocated = sum(sum(day.values()) for day in credited.values())
        if allocated != total:
            problems.append({"check": "activity_attribution", "teacher_id": person.get("teacher_id")})
        evidence_ids = [row.get("activity_id") for row in person.get("evidence", [])]
        if len(evidence_ids) != len(set(evidence_ids)):
            problems.append({"check": "unique_evidence", "teacher_id": person.get("teacher_id")})
    if all(isinstance(person.get("department"), str) and person["department"] for person in people):
        reconciliation = reconcile_department_totals(body.get("department", {}), people)
        if not reconciliation["ok"]:
            problems.extend({"check": "department_total", **row}
                             for row in reconciliation["discrepancies"])
    return {"ok": not problems, "problems": problems,
            "teacher_count": len(people),
            "evidence_count": sum(len(item.get("evidence", [])) for item in people)}
