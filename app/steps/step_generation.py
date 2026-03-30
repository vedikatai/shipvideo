# app/steps/step_generation.py — full file, replace entirely

"""
Step generation: produce a grounded list of capture steps (and narration) from
PR diff + live DOM.

Changes from previous version:
- Two-phase LLM: extraction call first, planning call second.
- validate_against_dom now annotates instead of dropping click steps.
- preflight_gate blocks invalid plans before browser opens.
- One replan attempt when preflight fails, then ContractIntegrityError.
- normalize_steps bug fixed: validation metadata now actually preserved.
- assert_terminal injected into last click when contract has terminal condition.
"""
from __future__ import annotations

import json
import os
import fnmatch
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
from observability import pipeline_step

try:
    from app.steps.errors import ContractIntegrityError
except ImportError:
    ContractIntegrityError = RuntimeError  # type: ignore

try:
    from observability.tracing import record_contract_integrity_error
except ImportError:
    def record_contract_integrity_error(*a, **kw) -> None:  # type: ignore
        pass

try:
    from openai import OpenAI, BadRequestError  # type: ignore
except Exception:
    OpenAI = None  # type: ignore
    BadRequestError = Exception  # type: ignore

FALLBACK_STEPS: List[Dict[str, Any]] = [{"action": "screenshot"}]


# ------------------------------------------------------------------ #
# JSON schemas                                                         #
# ------------------------------------------------------------------ #

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


# ------------------------------------------------------------------ #
# LLM caller                                                           #
# ------------------------------------------------------------------ #

def _call_llm(
    client: Any,
    model: str,
    messages: List[Dict[str, str]],
    max_completion_tokens: int,
    *,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """
    Call Azure OpenAI with structured output.
    Falls back to json_object if json_schema is unsupported.
    """
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


# ------------------------------------------------------------------ #
# Extraction phase                                                     #
# ------------------------------------------------------------------ #

def _run_extraction_phase(
    client: Any,
    model: str,
    diff_text: str,
    pr_title: Optional[str],
    contract: Optional[Any],
    max_tokens: int,
) -> Tuple[Dict[str, Any], float]:
    """
    Phase 1: extract start_route, terminal_testid, click_labels from diff only.
    Returns (extraction_data, cost_usd).
    """
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

    # If contract already has high-confidence data, skip LLM extraction
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

    # Include contract hints if partially available
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


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _fallback_narration(pr_title: Optional[str]) -> str:
    if pr_title:
        return f"Demo screenshot for pull request: {pr_title}."
    return "Demo screenshot for this pull request."


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


def _inject_terminal_assertion(
    steps: List[Dict[str, Any]],
    contract: Optional[Any],
) -> List[Dict[str, Any]]:
    """
    Inject an assert_terminal step after the last click if:
    - contract has a terminal condition
    - no assert_terminal step already exists
    """
    if contract is None:
        return steps

    terminal = getattr(contract, "terminal", None)
    if not terminal:
        return steps

    has_terminal = any(s.get("action") == "assert_terminal" for s in steps)
    if has_terminal:
        return steps

    # Find last click step and insert assert_terminal after it
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
    """
    Attach validation_condition to the last click before terminal assertion.

    This lets run_ab_stepwise detect wrong clicks immediately instead of only
    failing at the final assert_terminal step.
    """
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


def _build_planning_prompt(
    pr_title: Optional[str],
    extraction: Dict[str, Any],
    real_routes: List[str],
    real_buttons: List[Any],
    real_links: List[Any],
    real_inputs: List[Any],
    real_data_testids: List[Any],
    diff_text: str,
    app_hints_text: str,
    preflight_errors: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Build system + user messages for the planning LLM call."""

    extraction_block = ""
    if extraction:
        lines = ["=== EXTRACTED JOURNEY FACTS ==="]
        lines.append("These are extracted from the changed code. Trust them.")
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
        "• For click steps use exact visible label from real_buttons/real_links.\n"
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
            "real_buttons": real_buttons,
            "real_links": real_links,
            "real_inputs": real_inputs,
            "data_testids": real_data_testids,
            # Include budgeted diff for context but not as primary signal
            "diff_summary": diff_text[:2000] if diff_text else "",
        },
        ensure_ascii=False,
    )

    return system_msg, user_msg


# ------------------------------------------------------------------ #
# Main entry point                                                     #
# ------------------------------------------------------------------ #

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
    """
    Two-phase step generation:
    Phase 1 — extraction LLM call: start_route, terminal_testid, click_labels.
    Phase 2 — planning LLM call: full step list grounded in extraction + DOM.

    Pre-flight gate validates plan against contract before returning.
    One replan attempt on pre-flight failure. ContractIntegrityError on second failure.
    """
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

        # ---------------------------------------------------------- #
        # Config + seed routes                                         #
        # ---------------------------------------------------------- #
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

        # ---------------------------------------------------------- #
        # DOM crawl                                                    #
        # ---------------------------------------------------------- #
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

        real_buttons = dom_data.get("buttons") or []
        real_links = dom_data.get("links") or []
        real_inputs = dom_data.get("inputs") or []
        real_data_testids = dom_data.get("data_testids") or []

        diffs_for_prompt = [
            {"path": f["path"], "status": f["status"], "patch": f.get("patch", "")}
            for f in diff_files
        ]
        budgeted, _ = budget_diff_files(diffs_for_prompt)
        diff_text = json.dumps(budgeted, ensure_ascii=False)

        if should_skip_llm_for_size(len(diff_text)):
            return {
                "steps": FALLBACK_STEPS,
                "narration": fallback_narration,
                "llm_cost_usd": 0.0,
            }

        # ---------------------------------------------------------- #
        # Azure client                                                 #
        # ---------------------------------------------------------- #
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not (OpenAI and azure_endpoint and azure_key and azure_deployment):
            raise RuntimeError("Azure OpenAI is not configured correctly")

        base_url = azure_endpoint.rstrip("/")
        if not base_url.endswith("openai/v1"):
            base_url = base_url + "/openai/v1/"
        client = OpenAI(base_url=base_url, api_key=azure_key)

        # ---------------------------------------------------------- #
        # Phase 1: Extraction                                          #
        # ---------------------------------------------------------- #
        extraction, extraction_cost = _run_extraction_phase(
            client,
            azure_deployment,
            diff_text,
            pr_title,
            contract,
            get_max_completion_tokens(),
        )
        total_cost += extraction_cost

        # Override start_route from extraction if not explicitly provided
        if not start_route and extraction.get("start_route"):
            start_route = extraction["start_route"].strip()
            if start_route and start_route != "/":
                allowed_routes_override = {start_route}

        # ---------------------------------------------------------- #
        # Phase 2: Planning (with optional replan on preflight fail)   #
        # ---------------------------------------------------------- #
        preflight_errors: Optional[List[str]] = None

        for attempt in range(2):
            system_msg, user_msg = _build_planning_prompt(
                pr_title=pr_title,
                extraction=extraction,
                real_routes=real_routes,
                real_buttons=real_buttons,
                real_links=real_links,
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

            # Ensure at least one screenshot
            if not any(
                isinstance(s, dict) and s.get("action") == "screenshot"
                for s in steps
            ):
                steps.append({"action": "screenshot"})

            # Enforce start route
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

            # Inject terminal assertion from contract
            steps = _inject_terminal_assertion(steps, contract)
            # Inject validation metadata on the last critical click.
            steps = _inject_click_validation_from_terminal(steps, contract)

            # DOM reconciliation (annotates, does not drop clicks)
            dom_grounded = validate_against_dom(
                steps,
                dom_data,
                diff_files,
                allowed_routes_override=allowed_routes_override,
                contract=contract,
            )

            validated = validate_steps(dom_grounded)
            if not validated:
                validated = FALLBACK_STEPS

            normalized = normalize_steps(validated)
            if not normalized:
                normalized = FALLBACK_STEPS

            normalized = _ensure_screenshots_for_visited_pages(normalized)

            # -------------------------------------------------- #
            # Normalization integrity check                        #
            # -------------------------------------------------- #
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

            # -------------------------------------------------- #
            # Pre-flight gate                                      #
            # -------------------------------------------------- #
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
                preflight_errors = [
                    "Degenerate plan: zero click steps after normalization. "
                    "Regenerate a real demo flow with explicit click steps that reach the required target and terminal condition.",
                ]
            else:
                preflight_errors = preflight.errors

            if attempt == 1:
                # Second attempt also failed — hard abort
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
                "real_buttons": real_buttons,
                "real_links": real_links,
                "real_inputs": real_inputs,
                "data_testids": real_data_testids,
                "start_route": start_route,
                "suggested_demo_flow": suggested_demo_flow,
                "app_hints": app_hints_text,
                "contract": contract,
                "extraction": extraction,
            },
        }

    except ContractIntegrityError:
        raise  # Do not swallow — let pipeline handle it

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
