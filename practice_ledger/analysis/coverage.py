from collections import defaultdict

from .attribution import allocate_minutes


def group_records(records, key):
    grouped = defaultdict(list)
    for record in records:
        grouped[record[key]].append(record)
    return grouped


def company_coverage(records, period_start, period_end, roster_ids=None):
    """汇总实践基地投入，并避免重复计算重叠时段。"""
    records = list(records)
    if roster_ids is not None:
        accepted = set(roster_ids)
        records = [record for record in records if record["teacher_id"] in accepted]
    by_teacher = group_records(records, "teacher_id")
    credited = {}
    for teacher_id, items in by_teacher.items():
        daily = allocate_minutes(items, period_start, period_end)
        totals = defaultdict(int)
        for day in daily.values():
            for activity_id, minutes in day.items():
                totals[activity_id] += minutes
        credited.update(totals)
    activities = {item["activity_id"]: item for item in records}
    by_company = defaultdict(lambda: {"minutes": 0, "teachers": set(), "activities": 0, "roles": set()})
    for activity_id, item in activities.items():
        row = by_company[item["company"]]
        row["minutes"] += credited.get(activity_id, 0)
        row["teachers"].add(item["teacher_id"])
        row["activities"] += 1
        row["roles"].add(item["role"])
    total_minutes = sum(row["minutes"] for row in by_company.values())
    result = []
    for company, row in sorted(by_company.items()):
        result.append({"company": company, "minutes": row["minutes"],
                       "teacher_count": len(row["teachers"]), "activity_count": row["activities"],
                       "role_count": len(row["roles"]),
                       "share": row["minutes"] / total_minutes if total_minutes else 0.0})
    return {"total_minutes": total_minutes, "companies": result}


def role_coverage(records, period_start, period_end):
    """按活动岗位统计已审核实践覆盖情况。"""
    records = list(records)
    by_teacher = group_records(records, "teacher_id")
    credited = {}
    for teacher_id, items in by_teacher.items():
        for day in allocate_minutes(items, period_start, period_end).values():
            for activity_id, minutes in day.items():
                credited[activity_id] = minutes
    summary = defaultdict(lambda: {"minutes": 0, "teachers": set(), "companies": set()})
    for item in records:
        row = summary[item["role"]]
        row["minutes"] += credited.get(item["activity_id"], 0)
        row["teachers"].add(item["teacher_id"])
        row["companies"].add(item["company"])
    return {role: {"minutes": value["minutes"], "teacher_count": len(value["teachers"]),
                   "company_count": len(value["companies"])}
            for role, value in sorted(summary.items())}


def validate_coverage_totals(coverage, expected_minutes):
    if type(expected_minutes) is not int or expected_minutes < 0:
        raise ValueError("expected_minutes必须为非负整数")
    actual = sum(row["minutes"] for row in coverage["companies"])
    if actual != expected_minutes:
        return {"ok": False, "expected": expected_minutes, "actual": actual,
                "difference": actual - expected_minutes}
    return {"ok": True, "expected": expected_minutes, "actual": actual, "difference": 0}


def merge_company_aliases(coverage, aliases):
    """合并报表展示别名但不修改来源事实和已计入分钟。"""
    if not isinstance(aliases, dict):
        raise ValueError("aliases必须为对象")
    merged = defaultdict(lambda: {"minutes": 0, "teacher_count": 0,
                                  "activity_count": 0, "source_names": set()})
    for row in coverage.get("companies", []):
        target = aliases.get(row["company"], row["company"])
        if not isinstance(target, str) or not target.strip():
            raise ValueError("企业别名必须为非空文本")
        result = merged[target.strip()]
        result["minutes"] += row["minutes"]
        result["teacher_count"] += row["teacher_count"]
        result["activity_count"] += row["activity_count"]
        result["source_names"].add(row["company"])
    return [{"company": name, "minutes": values["minutes"],
             "teacher_count": values["teacher_count"], "activity_count": values["activity_count"],
             "source_names": sorted(values["source_names"])}
            for name, values in sorted(merged.items())]
