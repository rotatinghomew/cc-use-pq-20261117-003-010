from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone


DAY_LIMIT_MINUTES = 480


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def daily_union(records, period_start, period_end, limit=DAY_LIMIT_MINUTES):
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    if end <= start:
        raise ValueError("报告结束日必须晚于开始日")
    if type(limit) is not int or limit < 1 or limit > 1440:
        raise ValueError("每日上限必须为1至1440分钟")
    spans = defaultdict(list)
    for record in records:
        left, right = utc(record["starts_at"]), utc(record["ends_at"])
        cursor = max(left, datetime.combine(start, time.min, timezone.utc))
        right = min(right, datetime.combine(end, time.min, timezone.utc))
        while cursor < right:
            next_day = datetime.combine(cursor.date() + timedelta(days=1), time.min, timezone.utc)
            boundary = min(right, next_day)
            spans[cursor.date()].append((cursor, boundary))
            cursor = boundary
    result = {}
    current = start
    while current < end:
        intervals = sorted(spans.get(current, ()))
        total = 0
        if intervals:
            left, right = intervals[0]
            for next_left, next_right in intervals[1:]:
                if next_left <= right:
                    right = max(right, next_right)
                else:
                    total += int((right - left).total_seconds() // 60)
                    left, right = next_left, next_right
            total += int((right - left).total_seconds() // 60)
        result[current.isoformat()] = min(total, limit)
        current += timedelta(days=1)
    return result
