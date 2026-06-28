

from __future__ import annotations

import json
import os
import fnmatch
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from app.steps.dom_crawler import crawl_dom_data
from app.steps.preflight import preflight_gate
from app.llm_guards import (
    check_budget,
    estimate_run_cost,
    get_max_completion_tokens,
    record_spend,
    should_skip_llm_for_size,
)
from app.steps.step_normalizer import (
    normalize_steps,
    validate_against_dom,
    validate_steps,
    _extract_routes_from_diff,
)
from app.steps.diff_budget import budget_diff_files
from app.config import load_config
from app.steps.demo_contract import TargetRef
from observability import pipeline_step

try:
    from app.steps.errors import ContractIntegrityError
except ImportError:
    ContractIntegrityError = RuntimeError                

try:
    from observability.tracing import record_contract_integrity_error
except ImportError:
    def record_contract_integrity_error(*a, **kw) -> None:                
        pass

try:
    from openai import OpenAI, BadRequestError                
except Exception:
    OpenAI = None                
    BadRequestError = Exception                

FALLBACK_STEPS: List[Dict[str, Any]] = [{"action": "screenshot"}]






_EXTRACTION_JSON_SCHEMA: Dict[str, Any] = {
    "name": "extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "start_route": {
                "type": "string",
                "description": "The route path where the demo should begin, e.g. '/settings'.",
            },
            "terminal_testid": {
                "type": "string",
                "description": (
                    "The data-testid or element id that confirms the flow is complete, "
                    "e.g. 'recharge-success'. Empty string if not found."
                ),
            },
            "click_labels": {
                "type": "array",
                "description": "Ordered visible button/link labels the user must click through.",
                "items": {"type": "string"},
            },
            "interaction_hints": {
                "type": "array",
                "description": (
                    "Ordered prerequisite interaction hints inferred from the diff. "
                    "Use short natural-language phrases like 'select amount' or "
                    "'choose security option'. Empty array if none are evident."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["start_route", "terminal_testid", "click_labels", "interaction_hints"],
        "additionalProperties": False,
    },
}

_DEMO_FLOW_JSON_SCHEMA: Dict[str, Any] = {
    "name": "demo_flow",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "suggested_demo_flow": {
                "type": "string",
                "description": (
                    "2-3 sentence natural language narrative of the ideal demo session. "
                    "Written BEFORE steps to act as the guiding narrative."
                ),
            },
            "steps": {
                "type": "array",
                "description": "Ordered UI interaction steps for the demo.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["goto", "click", "screenshot", "assert_terminal"],
                        },
                        "url": {
                            "type": "string",
                            "description": "goto only: route path.",
                        },
                        "selector": {
                            "type": "string",
                            "description": "click only: semantic selector when no visible label.",
                        },
                        "text": {
                            "type": "string",
                            "description": "Legacy click field. Use label instead.",
                        },
                        "label": {
                            "type": "string",
                            "description": "click: exact visible label. screenshot: caption.",
                        },
                        "expected_element": {
                            "type": "string",
                            "description": "assert_terminal only: data-testid or id to confirm.",
                        },
                    },
                    "required": [
                        "action", "url", "selector",
                        "text", "label", "expected_element",
                    ],
                    "additionalProperties": False,
                },
            },
            "narration": {
                "type": "string",
                "description": "1-2 sentence script narrating the demo.",
            },
        },
        "required": ["suggested_demo_flow", "steps", "narration"],
        "additionalProperties": False,
    },
}






def _call_llm(
    client: Any,
    model: str,
    messages: List[Dict[str, str]],
    max_completion_tokens: int,
    *,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    schema = response_schema or _DEMO_FLOW_JSON_SCHEMA
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        content = completion.choices[0].message.content or "{}"
        print(
            f"[steps.step_generation] response_mode=json_schema "
            f"schema={schema.get('name', '?')}",
            flush=True,
        )
        return completion, json.loads(content)
    except BadRequestError as e:
        print(
            f"[steps.step_generation] json_schema unsupported ({type(e).__name__}); "
            f"retrying json_object",
            flush=True,
        )
    except Exception as e:
        err_str = str(e).lower()
        is_format_error = any(
            kw in err_str
            for kw in (
                "json_schema", "response_format",
                "unsupported", "invalid_request_error",
            )
        )
        if not is_format_error:
            raise
        print(
            f"[steps.step_generation] json_schema failed ({type(e).__name__}); "
            f"retrying json_object",
            flush=True,
        )

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        response_format={"type": "json_object"},
    )
    content = (completion.choices[0].message.content or "{}").strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start: end + 1]
    print("[steps.step_generation] response_mode=json_object (fallback)", flush=True)
    return completion, json.loads(content)






def _run_extraction_phase(
    client: Any,
    model: str,
    diff_text: str,
    pr_title: Optional[str],
    contract: Optional[Any],
    max_tokens: int,
) -> Tuple[Dict[str, Any], float]:
    interaction_hints: List[str] = []
    if contract is not None:
        try:
            for note in getattr(contract, "extraction_notes", []) or []:
                if not isinstance(note, str):
                    continue
                if note.startswith("interaction_hint_high:") or note.startswith("interaction_hint_low:"):
                    hint = note.split(":", 1)[1].strip()
                    if hint:
                        interaction_hints.append(hint)
                elif note.startswith("interaction_hint:"):
                    hint = note.split(":", 1)[1].strip()
                    if hint:
                        interaction_hints.append(hint)
        except Exception:
            interaction_hints = []


    if contract is not None:
        try:
            if (
                getattr(contract, "confidence", "low") in ("high", "medium")
                and getattr(contract, "targets", None)
            ):
                labels = [t.label for t in contract.targets if t.label]
                terminal = getattr(contract.terminal, "value", "") if contract.terminal else ""
                print(
                    "[steps.step_generation] extraction skipped — "
                    "using existing high-confidence contract",
                    flush=True,
                )
                return {
                    "start_route": getattr(contract, "start_route", ""),
                    "terminal_testid": terminal,
                    "click_labels": labels,
                    "interaction_hints": interaction_hints,
                }, 0.0
        except Exception:
            pass

    extraction_system = (
        "You are a code parser. "
        "Given a PR diff, extract exactly three things and return only JSON. "
        "No prose. No markdown. JSON only.\n\n"
        "Rules:\n"
        "- start_route: the new or modified route path the feature lives at.\n"
        "- terminal_testid: the data-testid or id on the element that confirms "
        "the flow completed (look for words like complete, success, done, finish). "
        "Empty string if not found.\n"
        "- click_labels: ordered list of exact visible button/link text the user "
        "must click to complete the flow. Extract from JSX button/link text only.\n"
        "- interaction_hints: ordered prerequisite setup interactions implied by the "
        "diff when they are reasonably explicit, such as selecting an amount, "
        "choosing a tab, toggling an option, or opening a drawer. Use [] when absent."
    )


    contract_hint = ""
    if contract is not None:
        try:
            existing_labels = [
                t.label for t in (contract.targets or []) if t.label
            ]
            if existing_labels:
                contract_hint = (
                    f"\n\nKnown click targets (confirm or correct these): "
                    f"{json.dumps(existing_labels)}"
                )
        except Exception:
            pass
        if interaction_hints:
            contract_hint += (
                f"\nKnown prerequisite interaction hints: "
                f"{json.dumps(interaction_hints)}"
            )

    extraction_user = json.dumps(
        {
            "pr_title": pr_title or "",
            "diff_files": diff_text,
        },
        ensure_ascii=False,
    ) + contract_hint

    messages = [
        {"role": "system", "content": extraction_system},
        {"role": "user", "content": extraction_user},
    ]

    completion, data = _call_llm(
        client, model, messages, max_tokens,
        response_schema=_EXTRACTION_JSON_SCHEMA,
    )

    cost = 0.0
    usage = getattr(completion, "usage", None)
    if usage is not None:
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        record_spend(pt, ct)
        cost = round(estimate_run_cost(pt, ct), 4)

    print(
        f"[steps.step_generation] extraction: "
        f"start_route={data.get('start_route')!r} "
        f"terminal={data.get('terminal_testid')!r} "
        f"labels={data.get('click_labels')} "
        f"hints={data.get('interaction_hints')}",
        flush=True,
    )
    return data, cost






def _fallback_narration(pr_title: Optional[str]) -> str:
    if pr_title:
        return f"Demo screenshot for pull request: {pr_title}."
    return "Demo screenshot for this pull request."


def _label_to_selector(label: str) -> str:
    normalized = "-".join(label.strip().lower().split())
    return f"[data-testid='{normalized}']" if normalized else ""


def _extract_changed_testids_from_diff(diff_files: List[Dict[str, str]]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for diff_file in diff_files:
        patch = str(diff_file.get("patch") or "")
        if not patch:
            continue
        for line in patch.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for match in re.finditer(r'data-testid=["\']([^"\']+)["\']', line):
                testid = str(match.group(1) or "").strip()
                if testid and testid not in seen:
                    seen.add(testid)
                    ordered.append(testid)
    return ordered


def _start_route_candidates(
    *,
    start_route: str,
    extraction: Dict[str, Any],
    real_routes: List[str],
) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()

    candidates: List[str] = []
    extracted_route = str(extraction.get("start_route") or "").strip()
    if extracted_route:
        candidates.append(extracted_route)
    if start_route:
        candidates.append(start_route)
    candidates.extend(str(route or "").strip() for route in real_routes)
    candidates.append("/")

    for route in candidates:
        if not route or not route.startswith("/"):
            continue
        if route in seen:
            continue
        seen.add(route)
        ordered.append(route)
    return ordered


def _upgrade_contract_from_extraction(
    contract: Optional[Any],
    extraction: Dict[str, Any],
) -> Optional[Any]:
    if contract is None:
        return None

    labels = []
    for raw_label in extraction.get("click_labels") or []:
        label = str(raw_label or "").strip()
        if label and label not in labels:
            labels.append(label)

    if not labels:
        return contract

    existing_targets = list(getattr(contract, "targets", []) or [])
    existing_keys = {
        (getattr(target, "label", "") or "").strip().casefold()
        for target in existing_targets
        if (getattr(target, "label", "") or "").strip()
    }

    augmented_targets = list(existing_targets)
    for label in labels:
        key = label.casefold()
        if key in existing_keys:
            continue
        existing_keys.add(key)
        augmented_targets.append(
            TargetRef(label=label, selector=_label_to_selector(label))
        )

    extraction_notes = list(getattr(contract, "extraction_notes", []) or [])
    extraction_notes.append("contract_targets_upgraded_from_extraction")

    return replace(
        contract,
        targets=augmented_targets,
        extraction_notes=extraction_notes,
    )


def _contract_confidence(contract: Optional[Any]) -> str:
    return str(getattr(contract, "confidence", "low") or "low").strip().lower()


def _can_attempt_direct_plan(contract: Optional[Any]) -> bool:
    return _contract_confidence(contract) == "high"


def _should_fallback_to_guarded_screenshot(contract: Optional[Any]) -> bool:
    return not _can_attempt_direct_plan(contract)


def _log_click_stage(stage: str, steps: List[Dict[str, Any]]) -> None:
    click_steps = [step for step in steps if isinstance(step, dict) and step.get("action") == "click"]
    payload = [
        {
            "label": step.get("label", ""),
            "selector": step.get("selector", ""),
            "text": step.get("text", ""),
            "dom_confirmed": step.get("dom_confirmed"),
            "match_confidence": step.get("match_confidence"),
        }
        for step in click_steps
    ]
    print(
        f"[steps.step_generation] {stage}: click_count={len(click_steps)} clicks={payload}",
        flush=True,
    )


# Soft prompt budget per interactive list; full counts stay in metadata so
# routes with 60+ controls (/settings, /admin) are not dropped from planning.
_ROUTE_CATALOG_INTERACTIVE_CAP = 50


def _route_snapshot_catalog(
    dom_data: Dict[str, Any],
    *,
    fallback_routes: List[str],
) -> Dict[str, Dict[str, Any]]:
    snapshots = dom_data.get("route_snapshots") or {}
    catalog: Dict[str, Dict[str, Any]] = {}
    for route in fallback_routes:
        route_dom = snapshots.get(route) or {}
        buttons = route_dom.get("buttons") or []
        links = route_dom.get("links") or []
        data_testids = route_dom.get("data_testids") or []
        button_entries = [
            {
                "text": (btn.get("text") or "").strip(),
                "selector": (btn.get("selector") or "").strip(),
                "testid": (btn.get("testid") or "").strip(),
                "aria": (btn.get("aria") or "").strip(),
            }
            for btn in buttons
            if (btn.get("text") or btn.get("selector") or "").strip()
        ]
        link_entries = [
            {
                "text": (link.get("text") or "").strip(),
                "href": (link.get("href") or "").strip(),
            }
            for link in links
            if (link.get("text") or "").strip()
        ]
        testid_entries = [
            (item.get("testid") or "").strip()
            for item in data_testids
            if (item.get("testid") or "").strip()
        ]
        interactive_total = len(button_entries) + len(link_entries)
        truncated = interactive_total > _ROUTE_CATALOG_INTERACTIVE_CAP
        # Prefer buttons first (demo clicks), then links, within the soft cap.
        remaining = _ROUTE_CATALOG_INTERACTIVE_CAP
        capped_buttons = button_entries[:remaining]
        remaining -= len(capped_buttons)
        capped_links = link_entries[: max(remaining, 0)]
        catalog[route] = {
            "buttons": capped_buttons,
            "links": capped_links,
            "data_testids": testid_entries[:_ROUTE_CATALOG_INTERACTIVE_CAP],
            "interactive_total": interactive_total,
            "button_total": len(button_entries),
            "link_total": len(link_entries),
            "truncated": truncated,
            "interactive_element_cap": _ROUTE_CATALOG_INTERACTIVE_CAP,
        }
    return catalog


def _find_link_target_for_click(
    step: Dict[str, Any],
    route_dom: Dict[str, Any],
) -> str:
    label = (step.get("label") or step.get("text") or "").strip().lower()
    selector = (step.get("selector") or "").strip()
    selector_testid = ""
    if selector.startswith("[data-testid='") and selector.endswith("']"):
        selector_testid = selector[len("[data-testid='"):-2].strip().lower()
    selector_aria = ""
    if selector.startswith("[aria-label='") and selector.endswith("']"):
        selector_aria = selector[len("[aria-label='"):-2].strip().lower()

    for link in route_dom.get("links") or []:
        href = (link.get("href") or "").strip()
        if not href.startswith("/"):
            continue
        link_text = (link.get("text") or "").strip().lower()
        link_testid = (link.get("testid") or "").strip().lower()
        link_aria = (link.get("aria") or "").strip().lower()
        if label and link_text and label == link_text:
            return href
        if selector_testid and link_testid and selector_testid == link_testid:
            return href
        if selector_aria and link_aria and selector_aria == link_aria:
            return href
    return ""


def _validate_against_route_snapshots(
    steps: List[Dict[str, Any]],
    dom_data: Dict[str, Any],
    diff_files: Optional[List[Dict[str, str]]],
    *,
    start_route: str,
    allowed_routes_override: Optional[set] = None,
    contract: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    route_snapshots = dom_data.get("route_snapshots") or {}
    accepted: List[Dict[str, Any]] = []
    current_route = start_route or "/"

    for step in steps:
        action = step.get("action")
        if action == "goto":
            accepted.extend(
                validate_against_dom(
                    [step],
                    dom_data,
                    diff_files,
                    allowed_routes_override=allowed_routes_override,
                    contract=contract,
                )
            )
            goto_url = (step.get("url") or "").strip()
            if goto_url:
                current_route = goto_url
            continue

        if action != "click":
            accepted.append(step)
            continue

        if current_route not in route_snapshots:
            label = str(step.get("label") or step.get("text") or "").strip()
            print(
                f"[planning] no route snapshot for '{current_route}' "
                f"— click '{label}' unverified by planning",
                flush=True,
            )
            accepted.append(
                {
                    **step,
                    "dom_confirmed": False,
                    "match_confidence": "none",
                    "dom_warning": f"Route '{current_route}' was not crawled",
                }
            )
            continue

        route_dom = route_snapshots[current_route]
        validated_clicks = validate_against_dom(
            [step],
            route_dom,
            diff_files,
            allowed_routes_override=allowed_routes_override,
            contract=contract,
        )
        if not validated_clicks:
            continue

        validated_click = validated_clicks[0]
        next_route = _find_link_target_for_click(validated_click, route_dom)
        if next_route:
            validated_click = {**validated_click, "expected_url": next_route}
            current_route = next_route
        accepted.append(validated_click)

    return accepted


def _synthesize_click_steps(
    extraction: Dict[str, Any],
    contract: Optional[Any],
    start_route: Optional[str],
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    route = (start_route or extraction.get("start_route") or "").strip()
    if route and route != "/":
        steps.append(
            {
                "action": "goto",
                "url": route,
                "selector": "",
                "text": "",
                "label": "",
                "expected_element": "",
            }
        )

    for raw_label in extraction.get("click_labels") or []:
        label = str(raw_label or "").strip()
        if not label:
            continue
        steps.append(
            {
                "action": "click",
                "url": "",
                "selector": "",
                "text": "",
                "label": label,
                "expected_element": "",
            }
        )

    steps = _inject_terminal_assertion(steps, contract)
    steps = _inject_click_validation_from_terminal(steps, contract)

    if not any(step.get("action") == "screenshot" for step in steps):
        steps.append(
            {
                "action": "screenshot",
                "url": "",
                "selector": "",
                "text": "",
                "label": "",
                "expected_element": "",
            }
        )

    return steps


def _ensure_screenshots_for_visited_pages(
    steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, step in enumerate(steps):
        out.append(step)
        action = step.get("action")
        if action not in {"goto", "click"}:
            continue
        next_action = (
            steps[i + 1].get("action")
            if i + 1 < len(steps) and isinstance(steps[i + 1], dict)
            else None
        )
        if next_action == "screenshot":
            continue
        auto_label = (
            "Auto-captured state after navigation"
            if action == "goto"
            else "Auto-captured state after interaction"
        )
        out.append({"action": "screenshot", "label": auto_label})
    return out


def _sanitize_terminal_assertions(
    steps: List[Dict[str, Any]],
    *,
    contract: Optional[Any] = None,
    real_data_testids: Optional[List[Any]] = None,
    diff_text: str = "",
) -> List[Dict[str, Any]]:
    """Drop assert_terminal steps that are not grounded in contract/DOM/diff."""
    terminal = getattr(contract, "terminal", None) if contract is not None else None
    contract_value = ""
    if terminal is not None:
        contract_value = str(getattr(terminal, "value", "") or "").strip().lower()

    known_testids = set()
    for item in real_data_testids or []:
        if isinstance(item, dict):
            tid = str(item.get("testid") or "").strip().lower()
        else:
            tid = str(item or "").strip().lower()
        if tid:
            known_testids.add(tid)

    diff_lower = (diff_text or "").lower()
    out: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("action") != "assert_terminal":
            out.append(step)
            continue

        condition = step.get("condition") if isinstance(step.get("condition"), dict) else {}
        expected = (
            str(condition.get("value") or "").strip()
            or str(step.get("expected_element") or "").strip()
            or str(step.get("expected_text") or "").strip()
            or str(step.get("expected_url") or "").strip()
        )
        expected_l = expected.lower()
        if not expected_l:
            continue

        grounded = False
        if contract_value and (
            expected_l == contract_value
            or contract_value in expected_l
            or expected_l in contract_value
        ):
            grounded = True
        if expected_l in known_testids:
            grounded = True
        if expected_l and expected_l in diff_lower:
            grounded = True

        if grounded:
            out.append(step)
    return out


def _inject_terminal_assertion(
    steps: List[Dict[str, Any]],
    contract: Optional[Any],
) -> List[Dict[str, Any]]:
    if contract is None:
        return steps

    terminal = getattr(contract, "terminal", None)
    if not terminal:
        return steps

    has_terminal = any(s.get("action") == "assert_terminal" for s in steps)
    if has_terminal:
        return steps


    last_click_idx = None
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].get("action") == "click":
            last_click_idx = i
            break

    terminal_step = {
        "action": "assert_terminal",
        "condition": {
            "type": getattr(terminal, "type", "element_present"),
            "value": getattr(terminal, "value", ""),
        },
        "expected_element": getattr(terminal, "value", ""),
    }

    if last_click_idx is not None:
        return (
            steps[: last_click_idx + 1]
            + [terminal_step]
            + steps[last_click_idx + 1 :]
        )

    return steps + [terminal_step]


def _inject_click_validation_from_terminal(
    steps: List[Dict[str, Any]],
    contract: Optional[Any],
) -> List[Dict[str, Any]]:
    if contract is None:
        return steps
    terminal = getattr(contract, "terminal", None)
    if not terminal:
        return steps

    term_type = str(getattr(terminal, "type", "") or "").strip()
    term_value = str(getattr(terminal, "value", "") or "").strip()
    if not term_type or not term_value:
        return steps

    term_index = None
    for i, s in enumerate(steps):
        if s.get("action") == "assert_terminal":
            term_index = i
            break

    search_upto = term_index if term_index is not None else len(steps)
    last_click_idx = None
    for i in range(search_upto - 1, -1, -1):
        if steps[i].get("action") == "click":
            last_click_idx = i
            break
    if last_click_idx is None:
        return steps

    validation_condition = {"type": term_type, "value": term_value}
    updated = list(steps)
    click_step = dict(updated[last_click_idx])
    click_step["validation_condition"] = validation_condition
    click_step.setdefault("success_condition", validation_condition)
    click_step["validation_source"] = "contract"
    updated[last_click_idx] = click_step
    return updated


def _inject_sequential_click_validations(
    steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    updated = [dict(step) for step in steps]
    click_indexes = [
        idx for idx, step in enumerate(updated)
        if step.get("action") == "click"
    ]
    if not click_indexes:
        return updated

    for offset, click_idx in enumerate(click_indexes):
        click_step = dict(updated[click_idx])
        if click_step.get("validation_condition") or click_step.get("success_condition"):
            updated[click_idx] = click_step
            continue

        expected_url = str(click_step.get("expected_url") or "").strip()
        if expected_url:
            condition = {"type": "url_match", "value": expected_url}
            click_step["validation_condition"] = condition
            click_step.setdefault("success_condition", condition)
            click_step.setdefault("validation_source", "planner_sequence")
            updated[click_idx] = click_step
            continue

        next_label = ""
        for next_idx in click_indexes[offset + 1:]:
            candidate = str(updated[next_idx].get("label") or "").strip()
            if candidate:
                next_label = candidate
                break

        if next_label:
            condition = {"type": "element_present", "value": next_label}
            click_step["validation_condition"] = condition
            click_step.setdefault("success_condition", condition)
            click_step.setdefault("validation_source", "planner_sequence")
            updated[click_idx] = click_step
            continue

        for later_step in updated[click_idx + 1:]:
            if later_step.get("action") != "assert_terminal":
                continue
            condition = later_step.get("condition")
            if isinstance(condition, dict) and condition.get("type") and condition.get("value"):
                click_step["validation_condition"] = dict(condition)
                click_step.setdefault("success_condition", dict(condition))
                click_step.setdefault("validation_source", "planner_sequence")
                updated[click_idx] = click_step
            break

    return updated


def _build_planning_prompt(
    pr_title: Optional[str],
    extraction: Dict[str, Any],
    real_routes: List[str],
    route_catalog: Dict[str, Dict[str, Any]],
    real_inputs: List[Any],
    real_data_testids: List[Any],
    diff_text: str,
    app_hints_text: str,
    preflight_errors: Optional[List[str]] = None,
) -> Tuple[str, str]:

    extraction_block = ""
    if extraction:
        lines = ["=== EXTRACTED JOURNEY FACTS ==="]
        lines.append("These are extracted from the changed code. Treat them as hints, not truth.")
        lines.append("")
        if extraction.get("start_route"):
            lines.append(f"START ROUTE: {extraction['start_route']}")
        if extraction.get("terminal_testid"):
            lines.append(
                f"TERMINAL CONDITION: element_present = "
                f"\"{extraction['terminal_testid']}\""
            )
            lines.append(
                "The flow is NOT complete until this element is present. "
                "Do NOT stop before it."
            )
        if extraction.get("click_labels"):
            lines.append("EXPECTED CLICK TARGETS IN ORDER:")
            for lbl in extraction["click_labels"]:
                lines.append(f'  - "{lbl}"')
            lines.append("Use these exact label strings for click steps.")
        if extraction.get("interaction_hints"):
            lines.append("PREREQUISITE INTERACTION HINTS:")
            for hint in extraction["interaction_hints"]:
                lines.append(f'  - "{hint}"')
            lines.append(
                "If a later click target depends on one of these setup interactions, "
                "include an explicit earlier step for it."
            )
        lines.append("=== END EXTRACTED FACTS ===")
        extraction_block = "\n".join(lines)

    preflight_block = ""
    if preflight_errors:
        lines = ["=== PREVIOUS ATTEMPT FAILED PRE-FLIGHT — FIX THESE ==="]
        for err in preflight_errors:
            lines.append(f"  - {err}")
        lines.append(
            "You MUST fix every issue above. "
            "Do not omit any required click target. "
            "Do not stop before the terminal condition."
        )
        lines.append("=== END PRE-FLIGHT ERRORS ===")
        preflight_block = "\n".join(lines)

    hints_block = f"\nApp hints:\n{app_hints_text}\n" if app_hints_text else ""

    system_msg = (
        "You are a demo-flow generator for pull requests.\n"
        "Given extracted journey facts and a live DOM snapshot, "
        "produce a UI walkthrough that showcases the changed functionality.\n\n"
        + (extraction_block + "\n\n" if extraction_block else "")
        + (preflight_block + "\n\n" if preflight_block else "")
        + "Output order:\n"
        "1. FIRST write `suggested_demo_flow`: 2-3 sentence narrative.\n"
        "2. THEN generate `steps` following the narrative.\n"
        "3. THEN write `narration`.\n\n"
        "RULES — DO NOT VIOLATE:\n"
        "• First step must be goto to START ROUTE.\n"
        "• Every label in EXPECTED CLICK TARGETS must appear as a click step.\n"
        "• Every prerequisite interaction hint must be satisfied by an explicit earlier step when relevant.\n"
        "• Do not stop until TERMINAL CONDITION is reachable.\n"
        "• Last meaningful step must be assert_terminal with the terminal condition.\n"
        "• Use ONLY routes from real_routes for goto.\n"
        "• Plan route-by-route. A click is valid only if it exists on the CURRENT route in route_catalog.\n"
        "• After a goto, the CURRENT route becomes that url. If you click a link, assume navigation only when that link is listed for the CURRENT route.\n"
        "• For click steps use exact visible label from the CURRENT route's buttons/links.\n"
        "• Put visible targets in `label`, not `selector` or `text`.\n"
        "• Use `selector` only for [data-testid='x'] or [aria-label='x'] targets.\n"
        "• Never use raw CSS selectors like #id or .class.\n"
        "• Set unused fields to empty string \"\".\n"
        "• Always include at least one screenshot step.\n"
        "• Keep narration concise (1-2 sentences).\n"
        + hints_block
    )

    user_msg = json.dumps(
        {
            "title": pr_title,
            "real_routes": real_routes,
            "route_catalog": route_catalog,
            "real_inputs": real_inputs,
            "data_testids": real_data_testids,

            "diff_summary": diff_text[:2000] if diff_text else "",
        },
        ensure_ascii=False,
    )

    return system_msg, user_msg






@pipeline_step("step_generation")
async def generate_steps_from_diff(
    diff_files: List[Dict[str, str]],
    pr_title: Optional[str],
    staging_url: str,
    *,
    start_route: Optional[str] = None,
    general_demo: bool = False,
    contract: Optional[Any] = None,
) -> Dict[str, Any]:
    fallback_narration = _fallback_narration(pr_title)
    total_cost = 0.0

    try:
        print("[steps.step_generation] generating steps from diff", flush=True)

        if not check_budget():
            print(
                "[steps.step_generation] budget limit reached; using fallback",
                flush=True,
            )
            return {
                "steps": FALLBACK_STEPS,
                "narration": fallback_narration,
                "budget_exceeded": True,
                "llm_cost_usd": 0.0,
                "generation_context": None,
            }

        start_route = (start_route or "").strip()
        allowed_routes_override = None
        if start_route and start_route != "/":
            allowed_routes_override = {start_route}




        config = load_config()
        route_map: Dict[str, Any] = config.get("routeMap") or {}
        app_hints: Any = config.get("appHints") or ""

        seed_routes: List[str] = []
        mapped_routes: set = set()

        if not general_demo:
            diff_seed = sorted(_extract_routes_from_diff(diff_files))
            for f in diff_files:
                fpath = f.get("path") or ""
                for pattern, routes in route_map.items():
                    if not pattern:
                        continue
                    if fnmatch.fnmatch(fpath, pattern):
                        if isinstance(routes, str):
                            if routes.strip():
                                mapped_routes.add(routes.strip())
                        elif isinstance(routes, list):
                            for r in routes:
                                if isinstance(r, str) and r.strip():
                                    mapped_routes.add(r.strip())

            _seen: set = set()
            for r in diff_seed + sorted(mapped_routes):
                if r not in _seen:
                    _seen.add(r)
                    seed_routes.append(r)

        if allowed_routes_override:
            seed_routes = [r for r in seed_routes if r in allowed_routes_override]




        dom_data = await crawl_dom_data(staging_url, seed_routes=seed_routes)
        real_routes = dom_data.get("routes") or ["/"]

        if not general_demo and mapped_routes:
            dom_data["routes"] = list(
                set((dom_data.get("routes") or []) + list(mapped_routes))
            )
            real_routes = dom_data["routes"]

        if isinstance(app_hints, dict):
            app_hints_text = "\n".join(
                [f"- {k}: {v}" for k, v in app_hints.items()]
            )
        else:
            app_hints_text = str(app_hints or "").strip()

        if allowed_routes_override:
            real_routes = list(allowed_routes_override | {"/"})

        real_inputs = dom_data.get("inputs") or []
        real_data_testids = dom_data.get("data_testids") or []

        diffs_for_prompt = [
            {"path": f["path"], "status": f["status"], "patch": f.get("patch", "")}
            for f in diff_files
        ]
        changed_testids = _extract_changed_testids_from_diff(diff_files)
        budgeted, _ = budget_diff_files(diffs_for_prompt)
        diff_text = json.dumps(budgeted, ensure_ascii=False)

        if should_skip_llm_for_size(len(diff_text)):
            return {
                "steps": FALLBACK_STEPS,
                "narration": fallback_narration,
                "llm_cost_usd": 0.0,
            }




        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not (OpenAI and azure_endpoint and azure_key and azure_deployment):
            raise RuntimeError("Azure OpenAI is not configured correctly")

        base_url = azure_endpoint.rstrip("/")
        if not base_url.endswith("openai/v1"):
            base_url = base_url + "/openai/v1/"
        client = OpenAI(base_url=base_url, api_key=azure_key)




        extraction, extraction_cost = _run_extraction_phase(
            client,
            azure_deployment,
            diff_text,
            pr_title,
            contract,
            get_max_completion_tokens(),
        )
        total_cost += extraction_cost

        contract = _upgrade_contract_from_extraction(contract, extraction)

        if contract is not None:
            print(
                f"[steps.step_generation] contract_after_extraction: "
                f"confidence={getattr(contract, 'confidence', 'low')} "
                f"targets={[getattr(target, 'label', '') for target in (getattr(contract, 'targets', []) or [])]}",
                flush=True,
            )

        if _should_fallback_to_guarded_screenshot(contract):
            start_candidates = _start_route_candidates(
                start_route=start_route,
                extraction=extraction,
                real_routes=real_routes,
            )
            initial_route = start_candidates[0] if start_candidates else "/"
            print(
                "[steps.step_generation] contract not strong enough for direct multi-step demo; "
                "entering discovery mode "
                f"testids={changed_testids} start_candidates={start_candidates}",
                flush=True,
            )
            return {
                "steps": [
                    {
                        "action": "goto",
                        "url": initial_route,
                    },
                    {
                        "action": "screenshot",
                        "label": "Initial state",
                    },
                ],
                "narration": fallback_narration,
                "suggested_demo_flow": "",
                "llm_cost_usd": total_cost,
                "generation_context": {
                    "dom_data": dom_data,
                    "diffs_for_prompt": diffs_for_prompt,
                    "real_routes": real_routes,
                    "route_catalog": {},
                    "real_inputs": real_inputs,
                    "data_testids": real_data_testids,
                    "changed_testids": changed_testids,
                    "start_route": initial_route,
                    "start_route_candidates": start_candidates,
                    "suggested_demo_flow": "",
                    "app_hints": app_hints_text,
                    "contract": contract,
                    "extraction": extraction,
                    "discovery_mode": True,
                },
            }

        if not start_route and extraction.get("start_route"):
            start_route = extraction["start_route"].strip()
            if start_route and start_route != "/":
                allowed_routes_override = {start_route}

        planning_routes = list(dict.fromkeys(([start_route] if start_route else []) + list(real_routes)))
        route_catalog = _route_snapshot_catalog(
            dom_data,
            fallback_routes=planning_routes,
        )




        preflight_errors: Optional[List[str]] = None

        for attempt in range(2):
            system_msg, user_msg = _build_planning_prompt(
                pr_title=pr_title,
                extraction=extraction,
                real_routes=real_routes,
                route_catalog=route_catalog,
                real_inputs=real_inputs,
                real_data_testids=real_data_testids,
                diff_text=diff_text,
                app_hints_text=app_hints_text,
                preflight_errors=preflight_errors,
            )

            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ]

            completion, data = _call_llm(
                client,
                azure_deployment,
                messages,
                get_max_completion_tokens(),
                response_schema=_DEMO_FLOW_JSON_SCHEMA,
            )

            usage = getattr(completion, "usage", None)
            if usage is not None:
                pt = getattr(usage, "prompt_tokens", 0) or 0
                ct = getattr(usage, "completion_tokens", 0) or 0
                record_spend(pt, ct)
                total_cost += round(estimate_run_cost(pt, ct), 4)

            steps = data.get("steps") or FALLBACK_STEPS
            _log_click_stage("raw_llm_steps", steps)


            if not any(
                isinstance(s, dict) and s.get("action") == "screenshot"
                for s in steps
            ):
                steps.append({"action": "screenshot"})


            if start_route and start_route != "/":
                goto_step = {
                    "action": "goto",
                    "url": start_route,
                    "selector": "",
                    "text": "",
                    "label": "",
                    "expected_element": "",
                }
                if not steps or steps[0].get("action") != "goto" or (
                    steps[0].get("url") or ""
                ).strip() != start_route:
                    steps = [goto_step] + steps


            steps = _inject_terminal_assertion(steps, contract)

            steps = _inject_click_validation_from_terminal(steps, contract)
            steps = _inject_sequential_click_validations(steps)


            dom_grounded = _validate_against_route_snapshots(
                steps,
                dom_data,
                diff_files,
                start_route=start_route or "/",
                allowed_routes_override=allowed_routes_override,
                contract=contract,
            )
            _log_click_stage("after_dom_grounding", dom_grounded)

            validated = validate_steps(dom_grounded)
            if not validated:
                validated = FALLBACK_STEPS

            normalized = normalize_steps(validated)
            if not normalized:
                normalized = FALLBACK_STEPS
            _log_click_stage("after_normalization", normalized)

            normalized = _ensure_screenshots_for_visited_pages(normalized)




            norm_click_idx = 0
            for raw_step in validated:
                if raw_step.get("action") != "click":
                    continue
                raw_has_validation = bool(
                    raw_step.get("success_condition")
                    or raw_step.get("validation_condition")
                )
                if raw_has_validation:
                    norm_clicks = [
                        s for s in normalized if s.get("action") == "click"
                    ]
                    norm_step = (
                        norm_clicks[norm_click_idx]
                        if norm_click_idx < len(norm_clicks)
                        else None
                    )
                    norm_has_validation = bool(
                        norm_step and (
                            norm_step.get("success_condition")
                            or norm_step.get("validation_condition")
                        )
                    )
                    if not norm_has_validation:
                        record_contract_integrity_error(
                            stage="normalization",
                            reason="validation_condition lost during normalize_steps",
                            contract_id=getattr(contract, "contract_id", "unknown"),
                        )
                        raise ContractIntegrityError(
                            stage="normalization",
                            field="validation_condition",
                            expected="present",
                            actual="missing",
                            contract_id=getattr(contract, "contract_id", "unknown"),
                        )
                norm_click_idx += 1




            preflight = preflight_gate(normalized, contract)

            if preflight.passed:
                print(
                    f"[steps.step_generation] preflight passed "
                    f"attempt={attempt + 1}",
                    flush=True,
                )
                break

            print(
                f"[steps.step_generation] preflight failed attempt={attempt + 1} "
                f"errors={preflight.errors}",
                flush=True,
            )
            if any(err.startswith("Degenerate plan: zero click steps") for err in preflight.errors):
                if extraction.get("click_labels"):
                    print(
                        "[steps.step_generation] zero-click plan detected; "
                        "synthesizing click steps from extraction labels",
                        flush=True,
                    )
                    synthesized = _synthesize_click_steps(extraction, contract, start_route)
                    synthesized = _inject_sequential_click_validations(synthesized)
                    _log_click_stage("synthesized_steps", synthesized)
                    dom_grounded = _validate_against_route_snapshots(
                        synthesized,
                        dom_data,
                        diff_files,
                        start_route=start_route or "/",
                        allowed_routes_override=allowed_routes_override,
                        contract=contract,
                    )
                    _log_click_stage("synthesized_after_dom_grounding", dom_grounded)
                    normalized = normalize_steps(validate_steps(dom_grounded))
                    _log_click_stage("synthesized_after_normalization", normalized)
                    normalized = _ensure_screenshots_for_visited_pages(normalized)
                    preflight = preflight_gate(normalized, contract)
                    if preflight.passed:
                        print(
                            f"[steps.step_generation] synthesized preflight passed "
                            f"attempt={attempt + 1}",
                            flush=True,
                        )
                        break
                preflight_errors = [
                    "Degenerate plan: zero click steps after normalization. "
                    "Regenerate a real demo flow with explicit click steps that reach the required target and terminal condition.",
                ]
            else:
                preflight_errors = preflight.errors

            if attempt == 1:

                record_contract_integrity_error(
                    stage="preflight",
                    reason="; ".join(preflight.errors),
                    contract_id=getattr(contract, "contract_id", "unknown"),
                    missing_targets=preflight.errors,
                )
                raise ContractIntegrityError(
                    stage="preflight",
                    field="plan_contract_match",
                    expected="all targets covered",
                    actual="; ".join(preflight.errors),
                    contract_id=getattr(contract, "contract_id", "unknown"),
                )

        narration = data.get("narration") or fallback_narration
        suggested_demo_flow = (data.get("suggested_demo_flow") or "").strip()

        print(
            f"[steps.step_generation] steps_generated={len(normalized)} "
            f"total_cost_usd={total_cost:.4f}",
            flush=True,
        )

        return {
            "steps": normalized,
            "narration": narration,
            "suggested_demo_flow": suggested_demo_flow,
            "llm_cost_usd": total_cost,
            "generation_context": {
                "dom_data": dom_data,
                "diffs_for_prompt": diffs_for_prompt,
                "real_routes": real_routes,
                "route_catalog": route_catalog,
                "real_inputs": real_inputs,
                "data_testids": real_data_testids,
                "changed_testids": changed_testids,
                "start_route": start_route,
                "suggested_demo_flow": suggested_demo_flow,
                "app_hints": app_hints_text,
                "contract": contract,
                "extraction": extraction,
            },
        }

    except ContractIntegrityError:
        raise                                           

    except Exception as e:
        print(
            f"[steps.step_generation] failed: {type(e).__name__}: {e}",
            flush=True,
        )
        return {
            "steps": FALLBACK_STEPS,
            "narration": fallback_narration,
            "budget_exceeded": False,
            "llm_cost_usd": total_cost,
            "generation_context": None,
        }
