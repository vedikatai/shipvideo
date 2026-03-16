"""
Step generation: produce a grounded list of capture steps (and narration) from PR diff + live DOM.

Uses Azure OpenAI with a strict JSON schema. Steps are validated and normalized against
the crawled DOM (routes, buttons, links, inputs, data-testids) so the LLM only sees
and can output real selectors/URLs. Fallback steps on budget/size/API errors.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from app.steps.dom_crawler import crawl_dom_data
from app.llm_guards import (
    check_budget,
    estimate_run_cost,
    get_max_tokens,
    record_spend,
    should_skip_llm_for_size,
)
from app.steps.step_normalizer import normalize_steps, validate_steps
from observability import pipeline_step

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

MAX_DIFF_CHARS = 8000

FALLBACK_STEPS: List[Dict[str, Any]] = [{"action": "screenshot"}]


def _fallback_narration(pr_title: Optional[str]) -> str:
    if pr_title:
        return f"Demo screenshot for pull request: {pr_title}."
    return "Demo screenshot for this pull request."


def _strip_markdown_and_extract_json(content: str) -> str:
    """Remove markdown code fences and slice out the first JSON object."""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if "\n" in text:
                first_line, rest = text.split("\n", 1)
                if first_line.strip().lower() in ("json", "javascript"):
                    text = rest
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


@pipeline_step("step_generation")
async def generate_steps_from_diff(
    diff_files: List[Dict[str, str]],
    pr_title: Optional[str],
    staging_url: str,
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
            }

        dom_data = await crawl_dom_data(staging_url)
        real_routes = dom_data.get("routes") or ["/"]
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

        system_msg = (
            "You are a tool that generates a short UI demo flow for a pull request.\n"
            "Return STRICT JSON ONLY with the shape:\n"
            '{"steps": [...], "narration": "..."}.\n'
            "Each step is a simple object like {\"action\": \"screenshot\"} or "
            "{ \"action\": \"goto\", \"url\": \"/billing\" } or "
            "{ \"action\": \"click\", \"selector\": \"[data-testid='x']\" } or "
            "{ \"action\": \"click\", \"text\": \"Button label\" }.\n"
            "Use only routes from real_routes. For clicks, use only selectors or button/link text "
            "from real_buttons and real_links.\n"
            "Prefer [data-testid='...'] when listed in real_buttons or data_testids; "
            'otherwise use "text": "exact visible text".\n'
            "Do not include any markdown or explanations."
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
        completion = client.chat.completions.create(
            model=azure_deployment,
            messages=messages,
            temperature=0.2,
            max_tokens=get_max_tokens(),
        )

        usage = getattr(completion, "usage", None)
        llm_cost_usd = 0.0
        if usage is not None:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            record_spend(pt, ct)
            llm_cost_usd = round(estimate_run_cost(pt, ct), 4)

        content = (completion.choices[0].message.content or "").strip()
        text = _strip_markdown_and_extract_json(content)
        data = json.loads(text)
        steps = data.get("steps") or FALLBACK_STEPS

        if not any(
            isinstance(s, dict) and s.get("action") == "screenshot" for s in steps
        ):
            print("[steps.step_generation] adding fallback screenshot step", flush=True)
            steps.append({"action": "screenshot"})

        validated = validate_steps(steps)
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
        }
