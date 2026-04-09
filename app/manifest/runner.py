from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import load_config
from app.steps.demo_contract import DemoContract, TargetRef, TerminalCondition
from app.steps.step_normalizer import _extract_routes_from_diff


_CLICK_PATTERN = re.compile(r'^Click\s+"([^"]+)"$', re.IGNORECASE)
_URL_PATTERN = re.compile(r'URL\s+(?:is|remains)\s+"([^"]+)"', re.IGNORECASE)
_TEXT_PATTERNS = (
    re.compile(r'text\s+"([^"]+)"', re.IGNORECASE),
    re.compile(r'shows?\s+"([^"]+)"', re.IGNORECASE),
    re.compile(r'visible with the text\s+"([^"]+)"', re.IGNORECASE),
)
_STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "from",
    "for",
    "in",
    "on",
    "of",
    "with",
    "flow",
    "demo",
}


@dataclass(frozen=True)
class ManifestFlow:
    name: str
    start_route: str
    click_labels: List[str]
    terminal_condition: TerminalCondition
    terminal_url: str = ""
    selection_reason: str = ""
    suggested_demo_flow: str = ""
    raw_success: str = ""


@dataclass(frozen=True)
class ManifestContext:
    pr_title: str = ""
    diff_files: List[Dict[str, str]] = field(default_factory=list)
    start_route: str = ""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _manifest_path() -> Path:
    config = load_config()
    configured = str(config.get("manifest_path") or "").strip()
    if configured:
        return (_repo_root() / configured).resolve()
    return _repo_root() / "shipvideodemo.json"


def _normalize_route(route: str) -> str:
    value = (route or "").strip()
    if not value:
        return "/"
    if not value.startswith("/"):
        value = "/" + value
    return value


def _parse_click_label(step: Any) -> str:
    if not isinstance(step, str):
        raise ValueError(f"manifest step must be a string, got {type(step).__name__}")
    match = _CLICK_PATTERN.fullmatch(step.strip())
    if not match:
        raise ValueError(f"unsupported manifest step format: {step!r}")
    label = match.group(1).strip()
    if not label:
        raise ValueError("manifest click label cannot be empty")
    return label


def _parse_terminal_condition(success: Any) -> tuple[TerminalCondition, str]:
    if not isinstance(success, str) or not success.strip():
        raise ValueError("manifest flow success must be a non-empty string")
    raw = success.strip()

    terminal_url = ""
    url_match = _URL_PATTERN.search(raw)
    if url_match:
        terminal_url = _normalize_route(url_match.group(1))

    for pattern in _TEXT_PATTERNS:
        text_match = pattern.search(raw)
        if text_match:
            text_value = text_match.group(1).strip()
            if text_value:
                return TerminalCondition(type="text_present", value=text_value), terminal_url

    quoted_values = [value.strip() for value in re.findall(r'"([^"]+)"', raw) if value.strip()]
    if terminal_url:
        quoted_values = [value for value in quoted_values if value != terminal_url]
    if quoted_values:
        return TerminalCondition(type="text_present", value=max(quoted_values, key=len)), terminal_url
    if terminal_url:
        return TerminalCondition(type="url_match", value=terminal_url), terminal_url
    raise ValueError(f"could not infer terminal condition from success: {raw!r}")


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def _load_manifest_flows() -> List[ManifestFlow]:
    path = _manifest_path()
    if not path.exists():
        return []

    with open(path) as f:
        payload = json.load(f)

    flows = payload.get("flows")
    if not isinstance(flows, list):
        raise ValueError("manifest must contain a top-level 'flows' array")

    parsed: List[ManifestFlow] = []
    for item in flows:
        if not isinstance(item, dict):
            raise ValueError("manifest flow entries must be objects")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("manifest flow missing name")
        start_route = _normalize_route(str(item.get("start") or item.get("start_route") or "/"))
        steps = item.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"manifest flow {name!r} must contain non-empty steps")
        click_labels = [_parse_click_label(step) for step in steps]
        terminal_condition, terminal_url = _parse_terminal_condition(item.get("success"))
        suggested_demo_flow = str(item.get("description") or item.get("narration") or "").strip()
        parsed.append(
            ManifestFlow(
                name=name,
                start_route=start_route,
                click_labels=click_labels,
                terminal_condition=terminal_condition,
                terminal_url=terminal_url,
                suggested_demo_flow=suggested_demo_flow,
                raw_success=str(item.get("success") or "").strip(),
            )
        )
    return parsed


def _score_flow(flow: ManifestFlow, ctx: ManifestContext) -> tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []
    explicit_route = _normalize_route(ctx.start_route) if ctx.start_route else ""
    changed_routes = _extract_routes_from_diff(ctx.diff_files) if ctx.diff_files else set()
    title = (ctx.pr_title or "").strip().lower()
    title_tokens = _tokens(title)
    flow_name_tokens = _tokens(flow.name)

    if explicit_route and explicit_route == flow.start_route:
        score += 10
        reasons.append(f"explicit_start_route={explicit_route}")

    if flow.start_route != "/" and flow.start_route in changed_routes:
        score += 6
        reasons.append(f"changed_start_route={flow.start_route}")

    if flow.terminal_url and flow.terminal_url in changed_routes:
        score += 5
        reasons.append(f"changed_terminal_url={flow.terminal_url}")

    overlap = sorted(flow_name_tokens & title_tokens)
    if overlap:
        score += len(overlap) * 2
        reasons.append(f"title_tokens={','.join(overlap)}")

    normalized_name = re.sub(r"\s+", " ", flow.name.strip().lower())
    if normalized_name and normalized_name in title:
        score += 6
        reasons.append("title_exact_match")

    return score, reasons


def get_manifest_flow(pr_context: Dict[str, Any]) -> Optional[ManifestFlow]:
    flows = _load_manifest_flows()
    if not flows:
        return None

    ctx = ManifestContext(
        pr_title=str(pr_context.get("pr_title") or "").strip(),
        diff_files=list(pr_context.get("diff_files") or []),
        start_route=str(pr_context.get("start_route") or "").strip(),
    )

    scored: List[tuple[int, ManifestFlow, List[str]]] = []
    for flow in flows:
        score, reasons = _score_flow(flow, ctx)
        scored.append((score, flow, reasons))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_flow, best_reasons = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1

    confident = best_score >= 6 and (len(scored) == 1 or best_score >= second_score + 2)
    if not confident:
        return None

    return ManifestFlow(
        name=best_flow.name,
        start_route=best_flow.start_route,
        click_labels=list(best_flow.click_labels),
        terminal_condition=best_flow.terminal_condition,
        terminal_url=best_flow.terminal_url,
        selection_reason="; ".join(best_reasons) or "manifest_match",
        suggested_demo_flow=best_flow.suggested_demo_flow,
        raw_success=best_flow.raw_success,
    )


def flow_to_steps(flow: ManifestFlow) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = [
        {"action": "goto", "url": flow.start_route},
        {"action": "screenshot", "label": "Initial state"},
    ]

    for index, label in enumerate(flow.click_labels):
        if index + 1 < len(flow.click_labels):
            next_label = flow.click_labels[index + 1]
            validation = {"type": "element_present", "value": next_label}
        else:
            validation = {
                "type": flow.terminal_condition.type,
                "value": flow.terminal_condition.value,
            }

        steps.append(
            {
                "action": "click",
                "label": label,
                "validation_condition": validation,
                "success_condition": validation,
                "validation_source": "manifest",
            }
        )
        steps.append({"action": "screenshot", "label": f"After clicking {label}"})

    steps.append(
        {
            "action": "assert_terminal",
            "condition": {
                "type": flow.terminal_condition.type,
                "value": flow.terminal_condition.value,
            },
            "expected_element": (
                flow.terminal_condition.value
                if flow.terminal_condition.type == "element_present"
                else ""
            ),
            "expected_text": (
                flow.terminal_condition.value
                if flow.terminal_condition.type == "text_present"
                else ""
            ),
            "expected_url": (
                flow.terminal_condition.value
                if flow.terminal_condition.type == "url_match"
                else ""
            ),
        }
    )
    steps.append({"action": "screenshot", "label": "Terminal state"})
    return steps


def flow_to_generation_context(flow: ManifestFlow) -> Dict[str, Any]:
    contract = DemoContract(
        start_route=flow.start_route,
        targets=[TargetRef(label=label) for label in flow.click_labels],
        terminal=flow.terminal_condition,
        confidence="high",
        source_static=True,
        extraction_notes=["manifest_flow_selected"],
    )
    return {
        "dom_data": {},
        "diffs_for_prompt": [],
        "real_routes": [flow.start_route],
        "route_catalog": {},
        "real_inputs": [],
        "data_testids": [],
        "changed_testids": [],
        "start_route": flow.start_route,
        "suggested_demo_flow": flow.suggested_demo_flow,
        "app_hints": "",
        "contract": contract,
        "extraction": {
            "start_route": flow.start_route,
            "terminal_testid": (
                flow.terminal_condition.value
                if flow.terminal_condition.type == "element_present"
                else ""
            ),
            "click_labels": list(flow.click_labels),
            "interaction_hints": [],
        },
        "manifest_flow": {
            "name": flow.name,
            "selection_reason": flow.selection_reason,
            "success": flow.raw_success,
        },
    }
