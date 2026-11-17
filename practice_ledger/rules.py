import re
from datetime import datetime, timezone


def text(value, field, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} 必须为1至{limit}个字符")
    return value.strip()


def identifier(value, field):
    result = text(value, field, 64)
    if not re.fullmatch(r"[\w][\w.:\-]*", result, flags=re.UNICODE):
        raise ValueError(f"{field} 格式无效")
    return result


def integer(value, field, minimum=0, maximum=10_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} 必须为{minimum}至{maximum}的整数")
    return value


def instant(value, field):
    raw = text(value, field, 40)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} 必须为含UTC偏移的ISO时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} 必须带UTC偏移")
    return parsed.astimezone(timezone.utc)


def interval(start, end):
    left, right = instant(start, "开始时间"), instant(end, "结束时间")
    if right <= left:
        raise ValueError("结束时间必须晚于开始时间")
    return left, right
