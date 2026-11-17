from collections import Counter


def quantile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def department_distribution(individuals, target_minutes):
    if type(target_minutes) is not int or target_minutes < 1:
        raise ValueError("target_minutes必须为正整数")
    groups = {}
    for item in individuals:
        groups.setdefault(item["department"], []).append(item)
    results = {}
    for department, members in sorted(groups.items()):
        totals = [row["total_minutes"] for row in members]
        bands = Counter("met" if value >= target_minutes else
                        "near" if value >= target_minutes * 0.75 else
                        "developing" if value > 0 else "none" for value in totals)
        results[department] = {
            "teacher_count": len(totals),
            "minimum": min(totals),
            "maximum": max(totals),
            "median": quantile(totals, 0.5),
            "lower_quartile": quantile(totals, 0.25),
            "upper_quartile": quantile(totals, 0.75),
            "bands": {name: bands[name] for name in ("met", "near", "developing", "none")},
        }
    return results


def compare_distribution(previous, current):
    names = previous.keys() | current.keys()
    changes = {}
    for name in sorted(names):
        old = previous.get(name, {})
        new = current.get(name, {})
        changes[name] = {key: new.get(key, 0) - old.get(key, 0)
                         for key in ("teacher_count", "minimum", "maximum", "median")}
    return changes


def outlier_candidates(individuals, *, minimum_group=4):
    groups = {}
    for item in individuals:
        groups.setdefault(item["department"], []).append(item)
    candidates = []
    for department, members in sorted(groups.items()):
        values = [row["total_minutes"] for row in members]
        if len(values) < minimum_group:
            continue
        lower = quantile(values, 0.25)
        upper = quantile(values, 0.75)
        spread = upper - lower
        floor = lower - 1.5 * spread
        ceiling = upper + 1.5 * spread
        for person in members:
            value = person["total_minutes"]
            if value < floor or value > ceiling:
                candidates.append({"department": department,
                                   "teacher_id": person["teacher_id"],
                                   "minutes": value,
                                   "lower_fence": floor,
                                   "upper_fence": ceiling,
                                   "review_required": True})
    return candidates
