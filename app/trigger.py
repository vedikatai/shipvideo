"""
Trigger evaluation: decide whether a PR diff warrants a demo run.

Exports:
  is_ui_file(path)          — shared classifier used here and by Phase 5 diff_budget.
  score_file(path) -> int   — 2/1/0 priority score for diff budgeting.
  evaluate_trigger(...)     — full trigger decision with mode/threshold/force logic.
  TriggerDecision           — dataclass returned by evaluate_trigger.

Extension/directory heuristic mirrors isUIFile() from:
  third_party/git-glimpse/packages/core/src/analyzer/diff-parser.ts
with the addition of `src/app/` as an explicit primary directory (Next.js `src/`
convention) and `src/components/`, `src/pages/`, `src/routes/` equivalents.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extension / directory constants (exported so diff_budget.py can import them)
# ---------------------------------------------------------------------------

UI_EXTENSIONS: frozenset = frozenset({
    ".tsx", ".jsx", ".ts", ".js",
    ".vue", ".svelte",
    ".css", ".scss",
    ".html",
})

# Patterns that disqualify a path from being a UI file regardless of extension.
NON_UI_PATTERNS: tuple = (
    ".test.", ".spec.", "__tests__",
    ".md",
    "package.json", "tsconfig",
    ".yml", ".yaml",
)

# Primary UI directories: direct rendered output.
# Phase 5 (diff_budget) imports this tuple — keep it exported at module level.
UI_PRIMARY_DIRS: tuple = (
    "app/", "src/app/",
    "components/", "src/components/",
    "pages/", "src/pages/",
    "routes/", "src/routes/",
    "extensions/", "blocks/",
)

# Secondary UI directories: UI-adjacent but less likely to directly affect rendering.
_UI_SECONDARY_DIRS: tuple = (
    "src/",         # catch-all for src/ files not under src/app/, src/components/, etc.
    "layouts/", "src/layouts/",
    "views/", "src/views/",
    "widgets/", "src/widgets/",
)


# ---------------------------------------------------------------------------
# Classifier helpers
# ---------------------------------------------------------------------------

def is_ui_file(path: str) -> bool:
    """
    Return True if path is likely a UI source file.

    Logic (mirrors isUIFile from Glimpse diff-parser.ts):
    1. Reject any path matching a non-UI pattern (.test., .spec., .md, etc.)
    2. Require a UI extension (.tsx, .jsx, .ts, .js, .vue, .svelte, .css, .scss, .html)
    3. Accept if path starts with any primary or secondary UI directory,
       OR if the path contains '/' (i.e. is not a lone root-level file).
    """
    for pattern in NON_UI_PATTERNS:
        if pattern in path:
            return False
    if not any(path.endswith(ext) for ext in UI_EXTENSIONS):
        return False
    for d in UI_PRIMARY_DIRS + _UI_SECONDARY_DIRS:
        if path.startswith(d):
            return True
    # Accept any file in a subdirectory (e.g. "lib/utils.ts"); reject lone root files
    return "/" in path


def score_file(path: str) -> int:
    """
    Return a diff-budget priority score:
      2 — primary UI  (direct rendered output, e.g. app/pricing/page.tsx)
      1 — secondary UI (adjacent, e.g. src/utils/format.ts)
      0 — non-UI      (tests, docs, config, etc.)

    Primary dirs are checked before secondary so that src/app/ scores 2, not 1.
    """
    for pattern in NON_UI_PATTERNS:
        if pattern in path:
            return 0
    if not any(path.endswith(ext) for ext in UI_EXTENSIONS):
        return 0
    for d in UI_PRIMARY_DIRS:
        if path.startswith(d):
            return 2
    for d in _UI_SECONDARY_DIRS:
        if path.startswith(d):
            return 1
    # File with a UI extension in a subdirectory but outside named dirs
    if "/" in path:
        return 1
    return 0


# ---------------------------------------------------------------------------
# TriggerDecision
# ---------------------------------------------------------------------------

@dataclass
class TriggerDecision:
    should_run: bool
    reason: str
    matched_files: List[str] = field(default_factory=list)
    general_demo: bool = False  # True → homepage-only crawl; skip feature-route seeding


# ---------------------------------------------------------------------------
# evaluate_trigger
# ---------------------------------------------------------------------------

def _file_magnitude(f: Dict[str, Any]) -> int:
    """
    Return additions+deletions for a diff file dict.

    Prefers explicit 'additions'/'deletions' keys (not present in the current
    fetch_pr_diff output). Falls back to counting hunk lines in the patch text.
    """
    explicit = int(f.get("additions") or 0) + int(f.get("deletions") or 0)
    if explicit > 0:
        return explicit
    patch = f.get("patch") or ""
    adds = sum(1 for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    dels = sum(1 for ln in patch.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return adds + dels


def evaluate_trigger(
    diff_files: List[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    force: bool = False,
) -> TriggerDecision:
    """
    Decide whether the pipeline should run for this diff.

    Modes (read from config["trigger"]["mode"]):
      auto      — run if any UI file changed (default when mode is absent or unrecognised)
      smart     — run only if matched UI files have additions+deletions >= threshold
      on-demand — skip unless force=True

    force=True overrides all mode checks and always returns should_run=True.
    """
    trigger_cfg: Dict[str, Any] = config.get("trigger") or {}
    mode: str = (trigger_cfg.get("mode") or "auto").lower()
    threshold: int = int(trigger_cfg.get("threshold") or 5)
    comment_cmd: str = trigger_cfg.get("commentCommand") or "/demo"

    if force:
        return TriggerDecision(
            should_run=True,
            reason="Force flag set; skipping all file filters.",
            matched_files=[f.get("path", "") for f in diff_files],
        )

    if mode == "on-demand":
        return TriggerDecision(
            should_run=False,
            reason=(
                f"on-demand mode: comment `{comment_cmd}` on this PR to generate a demo."
            ),
        )

    matched = [f for f in diff_files if is_ui_file(f.get("path") or "")]

    if not matched:
        return TriggerDecision(
            should_run=False,
            reason=(
                f"No UI-relevant files detected in diff. "
                f"Comment `{comment_cmd} --force` to generate a demo anyway."
            ),
        )

    if mode == "smart":
        magnitude = sum(_file_magnitude(f) for f in matched)
        if magnitude < threshold:
            return TriggerDecision(
                should_run=False,
                reason=(
                    f"Changes below smart threshold ({magnitude}/{threshold} lines changed). "
                    f"Comment `{comment_cmd} --force` to generate a demo anyway."
                ),
                matched_files=[f.get("path", "") for f in matched],
            )

    return TriggerDecision(
        should_run=True,
        reason=f"{len(matched)} UI file(s) changed.",
        matched_files=[f.get("path", "") for f in matched],
    )
