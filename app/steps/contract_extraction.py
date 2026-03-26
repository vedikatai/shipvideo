"""
Static contract extraction from PR diffs.

Derives start_route, targets, and terminal condition from file paths and
changed code without any LLM call. This is the primary independent contract
source that breaks circular validation.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from app.steps.demo_contract import DemoContract, TargetRef, TerminalCondition
from app.steps.step_normalizer import _extract_routes_from_diff


def extract_contract_static(
    diff_files: List[Dict[str, str]],
) -> DemoContract:
    """Build a DemoContract from static diff analysis only."""
    start_route = _infer_start_route(diff_files)
    targets = _extract_targets(diff_files)
    terminal = _detect_terminal(diff_files)
    
    notes: List[str] = []
    if not start_route:
        notes.append("no_start_route_inferred")
        start_route = "/"
    if not targets:
        notes.append("no_targets_extracted")
    if terminal is None:
        notes.append("no_terminal_detected")
    
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
        return sorted(routes)[0]  # Pick the first alphabetically
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
            # Extract data-testid values
            for m in re.finditer(r'data-testid=["\']([^"\']+)["\']', line):
                tid = m.group(1)
                label = tid.replace("-", " ").replace("_", " ")
                if label not in seen_labels:
                    seen_labels.add(label)
                    targets.append(TargetRef(
                        label=label,
                        selector=f"[data-testid='{tid}']",
                    ))
            # Extract button/link text from JSX
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
                # Try to extract the text content
                text_match = re.search(r'>\s*([^<]{3,50})\s*<', line)
                if text_match:
                    return TerminalCondition(
                        type="text_present",
                        value=text_match.group(1).strip(),
                    )
    return None
