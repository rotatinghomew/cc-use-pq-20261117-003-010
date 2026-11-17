from collections import defaultdict
from datetime import datetime, time, timedelta, timezone


def allocate_minutes(records, period_start, period_end, daily_limit=480):
    """按最早符合条件的记录确定性分摊每日并集。"""
    start = datetime.combine(datetime.fromisoformat(period_start).date(), time.min, timezone.utc)
    end = datetime.combine(datetime.fromisoformat(period_end).date(), time.min, timezone.utc)
    claims = defaultdict(list)
    for record in records:
        left = datetime.fromisoformat(record["starts_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        right = datetime.fromisoformat(record["ends_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        cursor = max(start, left)
        right = min(end, right)
        while cursor < right:
            midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, timezone.utc)
            boundary = min(right, midnight)
            claims[cursor.date().isoformat()].append((cursor, boundary, record))
            cursor = boundary
    result = {}
    for day, intervals in sorted(claims.items()):
        intervals.sort(key=lambda item: (item[0], item[1], item[2]["activity_id"]))
        cursor = None
        cap = daily_limit * 60
        credited = defaultdict(int)
        for left, right, record in intervals:
            effective_start = max(left, cursor) if cursor else left
            if effective_start >= right or cap <= 0:
                continue
            seconds = min(int((right - effective_start).total_seconds()), cap)
            if seconds <= 0:
                continue
            credited[record["activity_id"]] += seconds // 60
            cursor = effective_start + timedelta(seconds=seconds)
            cap -= seconds
        result[day] = dict(sorted(credited.items()))
    return result


def source_totals(records, period_start, period_end, daily_limit=480):
    by_teacher = defaultdict(list)
    for record in records:
        by_teacher[record["teacher_id"]].append(record)
    result = {}
    for teacher_id, items in sorted(by_teacher.items()):
        daily = allocate_minutes(items, period_start, period_end, daily_limit)
        totals = defaultdict(int)
        for activity_day in daily.values():
            for activity_id, minutes in activity_day.items():
                totals[activity_id] += minutes
        result[teacher_id] = dict(sorted(totals.items()))
    return result


def assert_allocations_do_not_exceed_union(records, period_start, period_end):
    from .intervals import daily_union
    union = daily_union(records, period_start, period_end)
    allocation = allocate_minutes(records, period_start, period_end)
    for day, total in union.items():
        allocated = sum(allocation.get(day, {}).values())
        if allocated != total:
            raise ValueError(f"归因与区间并集不一致：{day} {allocated}!={total}")
    return True
