from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.trigger import score_file


_TIER_BUDGET: Dict[int, int] = {
    2: 4000,                                               
    1: 1200,                                         
}


def budget_diff_files(
    diff_files: List[Dict[str, Any]],
    *,
    total_char_budget: int = 10_000,
) -> Tuple[List[Dict[str, Any]], bool]:
    was_truncated = False
    remaining = total_char_budget


    sorted_files = sorted(
        diff_files,
        key=lambda f: score_file(f.get("path") or ""),
        reverse=True,
    )

    budgeted: List[Dict[str, Any]] = []

    for f in sorted_files:
        path = f.get("path") or ""
        status = f.get("status") or ""
        original_patch = f.get("patch") or ""
        sc = score_file(path)

        if sc == 0:


            budgeted.append({
                "path": path,
                "status": status,
                "patch": f"# {path} changed (non-UI, omitted)",
            })
            continue

        per_file_limit = _TIER_BUDGET[sc]
        alloc = min(per_file_limit, remaining)

        if len(original_patch) <= alloc:

            budgeted.append({"path": path, "status": status, "patch": original_patch})
            remaining -= len(original_patch)
        else:

            budgeted.append({"path": path, "status": status, "patch": original_patch[:alloc]})
            remaining = max(0, remaining - alloc)
            was_truncated = True

    return budgeted, was_truncated
