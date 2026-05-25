from typing import Any, Dict, List, Tuple


def _normalize_results(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and "results" in data:
        results = data["results"]
    elif isinstance(data, list):
        results = data
    else:
        raise ValueError("results must be a list or a dict with a results field")

    if not isinstance(results, list):
        raise ValueError("results must be a list")
    return results


def _coerce_result(entry: Dict[str, Any]) -> Tuple[int, bool]:
    if not isinstance(entry, dict):
        raise ValueError("each result entry must be an object")
    if "level" not in entry or "solved" not in entry:
        raise ValueError("each result entry must include level and solved fields")
    level = entry["level"]
    if not isinstance(level, int):
        raise ValueError("level must be an integer")
    solved = entry["solved"]
    if not isinstance(solved, bool):
        raise ValueError("solved must be a boolean")
    return level, solved


def summarize_results(
    results: List[Dict[str, Any]],
    *,
    require_all: bool = True,
) -> Tuple[Dict[int, bool], Dict[int, int], Dict[int, int]]:
    counts: Dict[int, int] = {}
    solved_counts: Dict[int, int] = {}
    for entry in results:
        level, solved = _coerce_result(entry)
        counts[level] = counts.get(level, 0) + 1
        solved_counts[level] = solved_counts.get(level, 0) + (1 if solved else 0)

    per_level: Dict[int, bool] = {}
    for level in counts:
        if require_all:
            per_level[level] = solved_counts[level] == counts[level]
        else:
            per_level[level] = solved_counts[level] > 0
    return per_level, counts, solved_counts


def assess_highest_level(
    data: Any,
    *,
    require_all: bool = True,
) -> Dict[str, Any]:
    results = _normalize_results(data)
    per_level, counts, solved_counts = summarize_results(results, require_all=require_all)
    solved_levels = [level for level, solved in per_level.items() if solved]
    iq_level = max(solved_levels) if solved_levels else 0
    levels_summary = []
    for level in sorted(per_level):
        levels_summary.append(
            {
                "level": level,
                "solved": per_level[level],
                "solved_count": solved_counts[level],
                "total": counts[level],
            }
        )
    return {
        "iq_level": iq_level,
        "policy": "all" if require_all else "any",
        "levels": levels_summary,
        "results_count": len(results),
    }
