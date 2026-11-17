from collections import defaultdict
from datetime import date

from .intervals import daily_union


def month_key(value):
    return value[:7]


def month_range(start, end):
    first = date.fromisoformat(start).replace(day=1)
    last = date.fromisoformat(end)
    if last <= first:
        raise ValueError("结束日期须晚于开始日期")
    current = first
    while current < last:
        yield current.isoformat()[:7]
        current = current.replace(year=current.year + (current.month == 12),
                                  month=1 if current.month == 12 else current.month + 1)


def monthly_teacher_activity(records, period_start, period_end):
    months = list(month_range(period_start, period_end))
    by_teacher = defaultdict(list)
    for row in records:
        by_teacher[row["teacher_id"]].append(row)
    results = []
    for teacher_id, rows in sorted(by_teacher.items()):
        active_months = {}
        for month in months:
            month_start = month + "-01"
            year, number = map(int, month.split("-"))
            next_month = f"{year + (number == 12):04d}-{1 if number == 12 else number + 1:02d}-01"
            daily = daily_union(rows, month_start, next_month)
            active_months[month] = sum(daily.values())
        results.append({"teacher_id": teacher_id, "months": active_months,
                        "active_month_count": sum(value > 0 for value in active_months.values()),
                        "total_minutes": sum(active_months.values())})
    trend = []
    for month in months:
        values = [row["months"][month] for row in results]
        trend.append({"month": month, "teacher_count": len(results),
                      "participants": sum(value > 0 for value in values),
                      "total_minutes": sum(values)})
    return {"months": months, "teachers": results, "trend": trend}


def compare_months(series, left, right):
    known = set(series.get("months", []))
    if left not in known or right not in known:
        raise ValueError("比较月份必须存在于统计周期")
    changes = []
    for teacher in series.get("teachers", []):
        old = teacher["months"].get(left, 0)
        new = teacher["months"].get(right, 0)
        if old != new:
            changes.append({"teacher_id": teacher["teacher_id"], "previous_minutes": old,
                            "current_minutes": new, "delta_minutes": new - old})
    return {"previous_month": left, "current_month": right,
            "changed_teachers": changes,
            "net_minutes": sum(item["delta_minutes"] for item in changes)}
