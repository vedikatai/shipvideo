"""
Static contract extraction from PR diffs.

Derives start_route, targets, and terminal condition from file paths and
changed code without any LLM call. This is the primary independent contract
source that breaks circular validation.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from app.steps.demo_contract import DemoContract, TargetRef, TerminalCondition
from app.steps.step_normalizer import _extract_routes_from_diff


def extract_contract_static(
    diff_files: List[Dict[str, str]],
) -> DemoContract:
    """Build a DemoContract from static diff analysis only."""
    start_route = _infer_start_route(diff_files)
    targets = _extract_targets(diff_files)
    terminal = _detect_terminal(diff_files)
    interaction_hints = _extract_interaction_hints(diff_files)

    notes: List[str] = []
    if not start_route:
        notes.append("no_start_route_inferred")
        start_route = "/"
    if not targets:
        notes.append("no_targets_extracted")
    if terminal is None:
        notes.append("no_terminal_detected")
    for confidence, hint in interaction_hints:
        notes.append(f"interaction_hint_{confidence}:{hint}")

    confidence = "high" if (start_route != "/" and targets and terminal) else (
        "medium" if (targets or terminal) else "low"
    )

    return DemoContract(
        start_route=start_route,
        targets=targets,
        terminal=terminal,
        confidence=confidence,
        source_static=True,
        extraction_notes=notes,
    )


def _infer_start_route(diff_files: List[Dict[str, str]]) -> str:
    routes = _extract_routes_from_diff(diff_files)
    if routes:
        return sorted(routes)[0]                                 
    return ""


def _extract_targets(diff_files: List[Dict[str, str]]) -> List[TargetRef]:
    targets: List[TargetRef] = []
    seen_labels: Set[str] = set()

    for f in diff_files:
        patch = f.get("patch", "")
        if not patch:
            continue
        for line in patch.split("\n"):
            if not line.startswith("+"):
                continue

            for m in re.finditer(r'data-testid=["\']([^"\']+)["\']', line):
                tid = m.group(1)
                label = tid.replace("-", " ").replace("_", " ")
                if label not in seen_labels:
                    seen_labels.add(label)
                    targets.append(TargetRef(
                        label=label,
                        selector=f"[data-testid='{tid}']",
                    ))
            if not _line_looks_interactive(line):
                continue
            for m in re.finditer(r'>\s*([A-Z][A-Za-z\s]{2,30})\s*</', line):
                label = m.group(1).strip()
                if label not in seen_labels and len(label.split()) <= 5:
                    seen_labels.add(label)
                    targets.append(TargetRef(label=label))

    return targets


def _detect_terminal(diff_files: List[Dict[str, str]]) -> Optional[TerminalCondition]:
    terminal_patterns = re.compile(
        r'(complet|success|done|finish|confirm|submitted)',
        re.IGNORECASE,
    )
    for f in diff_files:
        patch = f.get("patch", "")
        for line in patch.split("\n"):
            if not line.startswith("+"):
                continue
            m = terminal_patterns.search(line)
            if m:

                text_match = re.search(r'>\s*([^<]{3,50})\s*<', line)
                if text_match:
                    return TerminalCondition(
                        type="text_present",
                        value=text_match.group(1).strip(),
                    )
    return None


def _extract_interaction_hints(diff_files: List[Dict[str, str]]) -> List[Tuple[str, str]]:
    hints: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    high_signal_patterns = (
        (re.compile(r"\bselect amount\b", re.IGNORECASE), "select amount"),
        (re.compile(r"\bchoose (plan|tier|option)\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\b(plan|tier|option) selected\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\bswitch (tab|tabs)\b", re.IGNORECASE), "switch tab"),
        (re.compile(r"\bopen (drawer|modal|sheet|panel)\b", re.IGNORECASE), "open panel"),
        (re.compile(r"\b(toggle|enable|disable) [A-Za-z]", re.IGNORECASE), "toggle option"),
        (re.compile(r"\b(check|uncheck) [A-Za-z]", re.IGNORECASE), "check option"),
        (re.compile(r"\bselect [A-Za-z].*(plan|tier|option|amount)\b", re.IGNORECASE), "choose option"),
    )
    low_signal_patterns = (
        (re.compile(r"\bamount\b", re.IGNORECASE), "select amount"),
        (re.compile(r"\b(tab|tabs)\b", re.IGNORECASE), "switch tab"),
        (re.compile(r"\b(plan|tier|option)\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\b(toggle|switch)\b", re.IGNORECASE), "toggle option"),
        (re.compile(r"\b(checkbox|check)\b", re.IGNORECASE), "check option"),
        (re.compile(r"\b(radio)\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\b(drawer|modal|sheet|panel)\b", re.IGNORECASE), "open panel"),
    )

    for f in diff_files:
        patch = f.get("patch", "")
        if not patch:
            continue
        for line in patch.split("\n"):
            if not line.startswith("+"):
                continue
            normalized = line[1:].strip()
            matched = False
            for pattern, hint in high_signal_patterns:
                if pattern.search(normalized):
                    entry = ("high", hint)
                    if entry not in seen:
                        seen.add(entry)
                        hints.append(entry)
                    matched = True
                    break
            if matched:
                continue
            for pattern, hint in low_signal_patterns:
                if pattern.search(normalized):
                    entry = ("low", hint)
                    if entry not in seen:
                        seen.add(entry)
                        hints.append(entry)
                    break
    return hints


def _line_looks_interactive(line: str) -> bool:
    return bool(
        re.search(r"<\s*(button|a)\b", line, re.IGNORECASE)
        or re.search(r'role=["\'](button|link|tab|menuitem)["\']', line, re.IGNORECASE)
        or re.search(r"<\s*[A-Za-z0-9_.:-]*(Button|Link|Tab|Checkbox|Radio)\b", line)
    )
