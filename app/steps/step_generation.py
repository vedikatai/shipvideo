"""
Step generation: produce a grounded list of capture steps (and narration) from
PR diff + live DOM.

This implements production-grade correctness:
- Structured output (JSON schema) when supported by the Azure deployment.
- DOM-grounded hard validation: we drop steps whose routes/selectors/text do
  not exist in the live crawl.
- Deterministic fallbacks on budget/size/API errors.
"""
from __future__ import annotations

import json
import os
import fnmatch
from typing import Any, Dict, List, Optional, Tuple

from app.steps.dom_crawler import crawl_dom_data
from app.llm_guards import (
    check_budget,
    estimate_run_cost,
    get_max_tokens,
    record_spend,
    should_skip_llm_for_size,
)
from app.steps.step_normalizer import normalize_steps, validate_against_dom, validate_steps
from app.config import load_config
from observability import pipeline_step

try:
    from openai import OpenAI, BadRequestError  # type: ignore
except Exception:
    OpenAI = None  # type: ignore
    BadRequestError = Exception  # type: ignore

MAX_DIFF_CHARS = 8000

FALLBACK_STEPS: List[Dict[str, Any]] = [{"action": "screenshot"}]


_DEMO_FLOW_JSON_SCHEMA: Dict[str, Any] = {
    "name": "demo_flow",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "Ordered UI interaction steps for the demo.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["goto", "click", "screenshot"],
                        },
                        "url": {
                            "type": "string",
                            "description": "goto only: route path (e.g. '/billing').",
                        },
                        "selector": {
                            "type": "string",
                            "description": "click only: prefer [data-testid='x'] selectors.",
                        },
                        "text": {
                            "type": "string",
                            "description": "click only: exact visible text when selector is not available.",
                        },
                        "label": {
                            "type": "string",
                            "description": "screenshot only: short caption for the frame.",
                        },
                    },
                    "required": ["action", "url", "selector", "text", "label"],
                    "additionalProperties": False,
                },
            },
            "narration": {"type": "string", "description": "1–2 sentence script narrating the demo."},
        },
        "required": ["steps", "narration"],
        "additionalProperties": False,
    },
}


def _call_llm(
    client: Any,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
) -> Tuple[Any, Dict[str, Any]]:
    """
    Call Azure OpenAI with structured output.

    If `json_schema` is unsupported by the deployment, retry with `json_object`
    and do minimal extraction.
    """
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": _DEMO_FLOW_JSON_SCHEMA},
        )
        content = completion.choices[0].message.content or "{}"
        print("[steps.step_generation] response_mode=json_schema", flush=True)
        return completion, json.loads(content)
    except BadRequestError as e:
        print(
            f"[steps.step_generation] json_schema unsupported ({type(e).__name__}); retrying json_object",
            flush=True,
        )
    except Exception as e:
        err_str = str(e).lower()
        is_format_error = any(
            kw in err_str for kw in ("json_schema", "response_format", "unsupported", "invalid_request_error")
        )
        if not is_format_error:
            raise
        print(
            f"[steps.step_generation] json_schema mode failed ({type(e).__name__}); retrying json_object",
            flush=True,
        )

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    content = (completion.choices[0].message.content or "{}").strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start : end + 1]
    print("[steps.step_generation] response_mode=json_object (fallback)", flush=True)
    return completion, json.loads(content)


def _fallback_narration(pr_title: Optional[str]) -> str:
    if pr_title:
        return f"Demo screenshot for pull request: {pr_title}."
    return "Demo screenshot for this pull request."


@pipeline_step("step_generation")
async def generate_steps_from_diff(
    diff_files: List[Dict[str, str]],
    pr_title: Optional[str],
    staging_url: str,
    *,
    start_route: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates capture steps and narration from PR diff + live DOM using Azure OpenAI.

    Phase: DOM crawl → build prompt (diff + grounded routes/buttons/links/inputs/testids)
    → LLM → parse JSON → validate + normalize steps.

    Returns:
        Dict with keys: steps, narration, llm_cost_usd; optionally budget_exceeded.
    """
    fallback_narration = _fallback_narration(pr_title)

    try:
        print("[steps.step_generation] generating steps from diff", flush=True)

        if not check_budget():
            print("[steps.step_generation] budget limit reached; using fallback steps", flush=True)
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

        dom_data = await crawl_dom_data(staging_url)
        real_routes = dom_data.get("routes") or ["/"]

        # ------------------------------------------------------------------
        # Diff → routeMap mapping + appHints injection (git-glimpse parity)
        # ------------------------------------------------------------------
        config = load_config()
        route_map: Dict[str, Any] = config.get("routeMap") or {}
        app_hints: Any = config.get("appHints") or ""

        mapped_routes: set[str] = set()
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

        if mapped_routes:
            dom_data["routes"] = list(set((dom_data.get("routes") or []) + list(mapped_routes)))
            real_routes = dom_data["routes"]

        # Normalize hints into a string for prompt injection.
        if isinstance(app_hints, dict):
            app_hints_text = "\n".join([f"- {k}: {v}" for k, v in app_hints.items()])
        else:
            app_hints_text = str(app_hints or "").strip()

        if allowed_routes_override:
            # Restrict prompt to the chosen route(s).
            real_routes = list(allowed_routes_override | {"/"})
        real_buttons = dom_data.get("buttons") or []
        real_links = dom_data.get("links") or []
        real_inputs = dom_data.get("inputs") or []
        real_data_testids = dom_data.get("data_testids") or []

        diffs_for_prompt = [
            {"path": f["path"], "status": f["status"], "patch": f.get("patch", "")}
            for f in diff_files
        ]
        diff_text = json.dumps(diffs_for_prompt, ensure_ascii=False)
        if len(diff_text) > MAX_DIFF_CHARS:
            diff_text = diff_text[:MAX_DIFF_CHARS]
            print(f"[steps.step_generation] truncated diff to {MAX_DIFF_CHARS} chars", flush=True)

        if should_skip_llm_for_size(len(diff_text)):
            return {
                "steps": FALLBACK_STEPS,
                "narration": fallback_narration,
                "llm_cost_usd": 0.0,
            }

        hints_block = f"\nApp hints:\n{app_hints_text}\n" if app_hints_text else ""

        system_msg = (
            "You are a demo-flow generator for pull requests.\n"
            "Given a PR diff and a live DOM snapshot of the staging preview, "
            "produce a short UI walkthrough that showcases the changed functionality.\n\n"
            "Rules:\n"
            "• Use ONLY routes from real_routes for goto actions.\n"
            "• For click actions use ONLY selectors from real_buttons or data_testids "
            "(prefer [data-testid='x']), OR the exact visible text from real_buttons / real_links.\n"
            "• Set unused fields (url / selector / text / label) to an empty string \"\".\n"
            "• Include 1–3 navigation/click steps that reach the changed areas, "
            "each followed by a screenshot.\n"
            "• Always include at least one screenshot step.\n"
            "• Keep narration concise (1–2 sentences).\n"
            + hints_block
        )

        user_msg = json.dumps(
            {
                "title": pr_title,
                "diff_files": diff_text,
                "real_routes": real_routes,
                "real_buttons": real_buttons,
                "real_links": real_links,
                "real_inputs": real_inputs,
                "data_testids": real_data_testids,
            },
            ensure_ascii=False,
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not (OpenAI and azure_endpoint and azure_key and azure_deployment):
            raise RuntimeError("Azure OpenAI is not configured correctly")

        print("[steps.step_generation] using Azure OpenAI backend", flush=True)
        base_url = azure_endpoint.rstrip("/")
        if not base_url.endswith("openai/v1"):
            base_url = base_url + "/openai/v1/"

        client = OpenAI(base_url=base_url, api_key=azure_key)

        completion, data = _call_llm(
            client,
            azure_deployment,
            messages,
            get_max_tokens(),
        )

        usage = getattr(completion, "usage", None)
        llm_cost_usd = 0.0
        if usage is not None:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            record_spend(pt, ct)
            llm_cost_usd = round(estimate_run_cost(pt, ct), 4)

        steps = data.get("steps") or FALLBACK_STEPS

        if not any(
            isinstance(s, dict) and s.get("action") == "screenshot" for s in steps
        ):
            print("[steps.step_generation] adding fallback screenshot step", flush=True)
            steps.append({"action": "screenshot"})

        # If we were told to start from a specific route, make sure we navigate
        # there before executing any clicks in capture.
        if start_route and start_route != "/":
            goto_step = {
                "action": "goto",
                "url": start_route,
                "selector": "",
                "text": "",
                "label": "",
            }
            if not steps or steps[0].get("action") != "goto" or (steps[0].get("url") or "").strip() != start_route:
                steps = [goto_step] + steps

        dom_grounded = validate_against_dom(
            steps,
            dom_data,
            diff_files,
            allowed_routes_override=allowed_routes_override,
        )

        validated = validate_steps(dom_grounded)
        if not validated:
            validated = FALLBACK_STEPS
        normalized = normalize_steps(validated)
        if not normalized:
            normalized = FALLBACK_STEPS

        narration = data.get("narration") or fallback_narration
        print(f"[steps.step_generation] steps_generated={len(normalized)}", flush=True)
        return {
            "steps": normalized,
            "narration": narration,
            "llm_cost_usd": llm_cost_usd,
            # Context needed for self-healing retries during execution.
            "generation_context": {
                "dom_data": dom_data,
                "diffs_for_prompt": diffs_for_prompt,
                "real_routes": real_routes,
                "real_buttons": real_buttons,
                "real_links": real_links,
                "real_inputs": real_inputs,
                "data_testids": real_data_testids,
                "start_route": start_route,
            },
        }

    except Exception as e:
        print(
            f"[steps.step_generation] failed: {type(e).__name__}: {e}",
            flush=True,
        )
        return {
            "steps": FALLBACK_STEPS,
            "narration": fallback_narration,
            "budget_exceeded": False,
            "llm_cost_usd": 0.0,
            "generation_context": None,
        }
