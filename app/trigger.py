from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)





UI_EXTENSIONS: frozenset = frozenset({
    ".tsx", ".jsx", ".ts", ".js",
    ".vue", ".svelte",
    ".css", ".scss",
    ".html",
})


NON_UI_PATTERNS: tuple = (
    ".test.", ".spec.", "__tests__",
    ".md",
    "package.json", "tsconfig",
    ".yml", ".yaml",
)



UI_PRIMARY_DIRS: tuple = (
    "app/", "src/app/",
    "components/", "src/components/",
    "pages/", "src/pages/",
    "routes/", "src/routes/",
    "extensions/", "blocks/",
)


_UI_SECONDARY_DIRS: tuple = (
    "src/",                                                                             
    "layouts/", "src/layouts/",
    "views/", "src/views/",
    "widgets/", "src/widgets/",
)






def is_ui_file(path: str) -> bool:
    for pattern in NON_UI_PATTERNS:
        if pattern in path:
            return False
    if not any(path.endswith(ext) for ext in UI_EXTENSIONS):
        return False
    for d in UI_PRIMARY_DIRS + _UI_SECONDARY_DIRS:
        if path.startswith(d):
            return True

    return "/" in path


def score_file(path: str) -> int:
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

    if "/" in path:
        return 1
    return 0






@dataclass
class TriggerDecision:
    should_run: bool
    reason: str
    matched_files: List[str] = field(default_factory=list)
    general_demo: bool = False                                                          






def _file_magnitude(f: Dict[str, Any]) -> int:
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
