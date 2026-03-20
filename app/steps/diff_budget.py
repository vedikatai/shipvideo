"""
Diff budgeting: rank-allocate patch content across diff files so the LLM
always sees the most UI-relevant changes first.

Replaces the blunt 8000-char string slice in step_generation.py with a
tiered allocation:

  score 2 (primary UI)   → up to 4000 chars of patch
  score 1 (secondary UI) → up to 1200 chars of patch
  score 0 (non-UI)       → stub comment; no char budget consumed

``score_file`` is imported from ``app.trigger`` (Phase 2) — extension and
directory sets are NOT redefined here to avoid divergence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.trigger import score_file

# Per-tier char budgets
_TIER_BUDGET: Dict[int, int] = {
    2: 4000,   # primary UI  (app/, pages/, components/, …)
    1: 1200,   # secondary UI (src/, lib/, utils/, …)
}


def budget_diff_files(
    diff_files: List[Dict[str, Any]],
    *,
    total_char_budget: int = 10_000,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Re-allocate patch content across diff files by relevance tier.

    Algorithm:
      1. Score each file via ``score_file`` (imported from app.trigger).
      2. Sort by score descending (stable: preserves PR order within same tier).
      3. Walk the sorted list accumulating against ``total_char_budget``:
           - score 0  → replace patch with a stub comment; no budget consumed.
           - score 1/2 → alloc = min(tier_budget, remaining).
                         If patch fits in alloc, include whole patch.
                         Otherwise truncate to alloc and mark was_truncated=True.
      4. Return (budgeted_files, was_truncated) in score-descending order so
         the LLM prompt always sees the most relevant changes first.

    Args:
        diff_files:         List of dicts with keys ``path``, ``status``, ``patch``.
        total_char_budget:  Hard upper bound on total patch chars in the output.

    Returns:
        (budgeted_files, was_truncated)
    """
    was_truncated = False
    remaining = total_char_budget

    # Stable sort: score descending; within same score, original order preserved
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
            # Non-UI file: replace with a single-line stub so the LLM knows
            # the file changed without consuming char budget for its full diff.
            budgeted.append({
                "path": path,
                "status": status,
                "patch": f"# {path} changed (non-UI, omitted)",
            })
            continue

        per_file_limit = _TIER_BUDGET[sc]
        alloc = min(per_file_limit, remaining)

        if len(original_patch) <= alloc:
            # Full patch fits within allocation
            budgeted.append({"path": path, "status": status, "patch": original_patch})
            remaining -= len(original_patch)
        else:
            # Truncate to available budget
            budgeted.append({"path": path, "status": status, "patch": original_patch[:alloc]})
            remaining = max(0, remaining - alloc)
            was_truncated = True

    return budgeted, was_truncated
