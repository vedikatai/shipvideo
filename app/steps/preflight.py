# app/steps/preflight.py  — new file, create it

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PreflightResult:
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    action: str = "proceed"  # "proceed" | "regenerate" | "abort"


def _parse_interaction_hints(contract: Any) -> Dict[str, List[str]]:
    hints: Dict[str, List[str]] = {"high": [], "low": []}
    for note in (getattr(contract, "extraction_notes", []) or []):
        if not isinstance(note, str):
            continue
        if note.startswith("interaction_hint_high:"):
            hint = note.split(":", 1)[1].strip().lower()
            if hint:
                hints["high"].append(hint)
        elif note.startswith("interaction_hint_low:"):
            hint = note.split(":", 1)[1].strip().lower()
            if hint:
                hints["low"].append(hint)
        elif note.startswith("interaction_hint:"):
            hint = note.split(":", 1)[1].strip().lower()
            if hint:
                hints["high"].append(hint)
    return hints


def preflight_gate(
    steps: List[Dict[str, Any]],
    contract: Optional[Any],
) -> PreflightResult:
    """
    Hard gate between planning and execution.
    A plan that fails this gate never opens the browser.

    Checks:
    1. Correct start route
    2. All required contract targets covered at acceptable confidence
    3. Explicit assert_terminal step exists and matches contract
    4. Plan is not degenerate (zero clicks)
    """
    if contract is None:
        # No contract — unguided run, warn but proceed
        return PreflightResult(
            passed=True,
            warnings=["No contract supplied — running unguided"],
            action="proceed",
        )

    errors: List[str] = []
    warnings: List[str] = []

    # ------------------------------------------------------------------ #
    # Gate 1: correct start route                                          #
    # ------------------------------------------------------------------ #
    start_route = getattr(contract, "start_route", None)
    if start_route:
        first_goto = next(
            (s for s in steps if s.get("action") == "goto"), None
        )
        if not first_goto:
            errors.append(f"No goto step found. Plan must start with goto {start_route}")
        elif (first_goto.get("url") or "").strip().rstrip("/") != start_route.rstrip("/"):
            errors.append(
                f"Plan starts at '{first_goto.get('url')}' "
                f"but contract requires '{start_route}'"
            )

    # ------------------------------------------------------------------ #
    # Gate 2: all required contract targets covered                        #
    # ------------------------------------------------------------------ #
    # Important policy:
    # - If a required target label appears in planned click steps, count it as covered
    #   even when dom_confirmed=False.
    # - This trusts extraction/contract labels for conditional UI that may not appear
    #   in static crawl snapshots until prior interactions are performed.
    click_steps = [s for s in steps if s.get("action") == "click"]
    click_labels_lower = [
        (s.get("label") or s.get("selector") or "").lower()
        for s in click_steps
    ]
    interaction_hints = _parse_interaction_hints(contract)

    try:
        for target in contract.targets or []:
            if not getattr(target, "required", True):
                continue

            target_label = (target.label or "").strip().lower()
            if not target_label:
                continue

            # Accept exact / fuzzy label presence in the plan.
            # Do NOT require dom_confirmed=True here.
            matched = any(
                target_label == cl
                or (len(target_label) > 4 and target_label in cl)
                or (len(cl) > 4 and cl in target_label)
                for cl in click_labels_lower
            )

            if not matched:
                errors.append(
                    f"Required contract target missing from plan: '{target.label}'"
                )
    except Exception as e:
        warnings.append(f"Could not validate contract targets: {e}")

    # ------------------------------------------------------------------ #
    # Gate 3: assert_terminal step exists                                  #
    # ------------------------------------------------------------------ #
    terminal = getattr(contract, "terminal", None)
    if terminal:
        terminal_steps = [s for s in steps if s.get("action") == "assert_terminal"]
        if not terminal_steps:
            errors.append(
                f"No assert_terminal step in plan. "
                f"Contract requires terminal condition: {terminal.value}"
            )
        else:
            last_terminal = terminal_steps[-1]
            condition = last_terminal.get("condition") or {}
            plan_value = (
                condition.get("value")
                or last_terminal.get("expected_element")
                or last_terminal.get("expected_text")
                or ""
            )
            if terminal.value not in str(plan_value):
                errors.append(
                    f"Terminal assertion value mismatch: "
                    f"plan has '{plan_value}', "
                    f"contract requires '{terminal.value}'"
                )

        click_before_terminal = None
        for step in reversed(steps):
            if step.get("action") == "assert_terminal":
                continue
            if step.get("action") == "click":
                click_before_terminal = step
                break
        if click_before_terminal is not None:
            has_validation = bool(
                click_before_terminal.get("validation_condition")
                or click_before_terminal.get("success_condition")
            )
            if not has_validation:
                errors.append(
                    "Last click before assert_terminal is missing validation metadata"
                )

    # ------------------------------------------------------------------ #
    # Gate 4: not a degenerate plan                                        #
    # ------------------------------------------------------------------ #
    if len(click_steps) == 0:
        errors.append(
            "Degenerate plan: zero click steps after normalization. "
            "This plan cannot demonstrate any feature."
        )

    # ------------------------------------------------------------------ #
    # Gate 5: obvious prerequisite setup coverage                         #
    # ------------------------------------------------------------------ #
    if interaction_hints["high"] or interaction_hints["low"]:
        earlier_clicks = click_steps[:-1] if len(click_steps) > 1 else []
        earlier_labels = [
            (s.get("label") or s.get("selector") or "").strip().lower()
            for s in earlier_clicks
        ]
        zero_click_plan = len(click_steps) == 0
        for hint in interaction_hints["high"]:
            if any(
                hint == label
                or (len(hint) > 4 and hint in label)
                or (len(label) > 4 and label in hint)
                for label in earlier_labels
            ):
                continue
            message = f"Missing prerequisite setup step implied by contract hint: '{hint}'"
            if zero_click_plan:
                warnings.append(message)
            else:
                errors.append(message)
        for hint in interaction_hints["low"]:
            if any(
                hint == label
                or (len(hint) > 4 and hint in label)
                or (len(label) > 4 and label in hint)
                for label in earlier_labels
            ):
                continue
            warnings.append(
                f"Weak prerequisite setup hint not covered explicitly: '{hint}'"
            )

    if errors:
        return PreflightResult(
            passed=False,
            errors=errors,
            warnings=warnings,
            action="regenerate",
        )

    return PreflightResult(passed=True, warnings=warnings, action="proceed")
