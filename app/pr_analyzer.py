"""
Analyzes PR diffs to generate dynamic capture flows (steps + narration)
using Azure OpenAI over the real code diff and grounded in the live DOM.
"""
import os
from typing import List, Dict, Optional, Any

import json
import requests

from app.step_normalizer import validate_steps, normalize_steps
from app.dom_crawler import crawl_dom_data
from app.llm_guards import (
    get_max_tokens,
    check_budget,
    record_spend,
    should_skip_llm_for_size,
)

try:
    # Use the generic OpenAI client against the Azure endpoint,
    # following the portal's code sample (base_url + deployment_name).
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore
MAX_DIFF_CHARS = 8000
MAX_DOM_CHARS = 2000

def fetch_pr_diff(repo_full_name: str, pr_number: int) -> List[Dict[str, str]]:
    """
    Fetches changed files and their diffs for a PR using the GitHub REST API.

    Each item has:
      - path: "app/pricing/page.tsx"
      - status: "added" | "modified" | "removed" | "renamed"
      - patch: unified diff (truncated)
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN not set in .env")

    result: List[Dict[str, str]] = []
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files"
    headers = {"Authorization": f"token {token}"}

    page = 1
    per_page = 100
    max_pages = 50  # Safety cap: avoid unbounded loop if API misbehaves (50 * 100 = 5000 files)
    while page <= max_pages:
        resp = requests.get(url, headers=headers, params={"page": page, "per_page": per_page})
        files = resp.json()
        if not files:
            break

        for f in files:
            patch = f.get("patch", "") or ""
            # truncate large diffs - LLM doesn't need 2000+ lines
            if len(patch) > 3000:
                patch = patch[:3000] + "\n... (truncated)"
            result.append(
                {
                    "path": f["filename"],
                    "status": f["status"],
                    "patch": patch,
                }
            )
            print(f"   [route-diff] file: {f['filename']} ({f['status']})", flush=True)

        if len(files) < per_page:
            break
        page += 1
    if page > max_pages:
        print(f"[route-diff] Reached max_pages ({max_pages}), stopping pagination", flush=True)
    print(f"[route-diff] fetched {len(result)} changed files", flush=True)
    return result


async def generate_steps_from_diff(
    diff_files: List[Dict[str, str]],
    pr_title: Optional[str],
    staging_url: str,
) -> Dict[str, Any]:
    """
    Phase 1 brain: takes real diff files + PR title and returns
    a flow dict with:
      - steps: list of capture steps for capture_demo()
      - narration: string script describing the demo

    Uses Azure OpenAI (via OpenAI client) and falls back deterministically on failure.
    """
    fallback_steps: List[Dict[str, Any]] = [{"action": "screenshot"}]
    fallback_narration = (
        f"Demo screenshot for pull request: {pr_title}."
        if pr_title
        else "Demo screenshot for this pull request."
    )

    try:
        print("🧠 [route-diff] Calling Azure OpenAI for step generation...", flush=True)

        if not check_budget():
            print("[route-diff] Budget limit reached; using fallback steps (no LLM call)", flush=True)
            return {"steps": fallback_steps, "narration": fallback_narration, "budget_exceeded": True}

        # Phase 2: DOM grounding – real routes and structured UI elements.
        dom_data = await crawl_dom_data(staging_url)
        real_routes = dom_data.get("routes") or ["/"]
        real_buttons = dom_data.get("buttons") or []
        real_links = dom_data.get("links") or []
        real_inputs = dom_data.get("inputs") or []
        real_data_testids = dom_data.get("data_testids") or []

        # Compact view of diffs for the prompt
        diffs_for_prompt = [
            {
                "path": f["path"],
                "status": f["status"],
                "patch": f.get("patch", ""),
            }
            for f in diff_files
        ]

        diff_text = json.dumps(diffs_for_prompt, ensure_ascii=False)
        if len(diff_text) > MAX_DIFF_CHARS:
            diff_text = diff_text[:MAX_DIFF_CHARS]
            print(
                f"[route-diff] Truncated diff payload for LLM to {MAX_DIFF_CHARS} characters",
                flush=True,
            )

        if should_skip_llm_for_size(len(diff_text)):
            return {"steps": fallback_steps, "narration": fallback_narration}

        system_msg = (
            "You are a tool that generates a short UI demo flow for a pull request.\n"
            "Return STRICT JSON ONLY with the shape:\n"
            '{\"steps\": [...], \"narration\": \"...\"}.\n'
            "Each step is a simple object like {\"action\": \"screenshot\"} or "
            "{ \"action\": \"goto\", \"url\": \"/billing\" } or "
            "{ \"action\": \"click\", \"selector\": \"[data-testid='x']\" } or { \"action\": \"click\", \"text\": \"Button label\" }.\n"
            "Use only routes from real_routes. For clicks, use only selectors or button/link text from real_buttons and real_links.\n"
            "Prefer [data-testid='...'] when listed in real_buttons or data_testids; otherwise use \"text\": \"exact visible text\".\n"
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

        # Use Azure OpenAI (via OpenAI client). If misconfigured, fall back to deterministic steps.
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not (OpenAI and azure_endpoint and azure_key and azure_deployment):
            raise RuntimeError("Azure OpenAI is not configured correctly")

        print("[route-diff] Using Azure OpenAI backend (OpenAI client)", flush=True)

        # Portal sample uses base_url ending with /openai/v1/
        base_url = azure_endpoint
        if not base_url.rstrip("/").endswith("openai/v1"):
            base_url = base_url.rstrip("/") + "/openai/v1/"

        client = OpenAI(
            base_url=base_url,
            api_key=azure_key,
        )
        completion = client.chat.completions.create(
            model=azure_deployment,
            messages=messages,
            temperature=0.2,
            max_tokens=get_max_tokens(),
        )

        usage = getattr(completion, "usage", None)
        if usage is not None:
            record_spend(
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )

        content = completion.choices[0].message.content or ""

        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                # Drop optional language tag line
                if "\n" in text:
                    first_line, rest = text.split("\n", 1)
                    if first_line.strip().lower() in ("json", "javascript"):
                        text = rest
            text = text.strip()

        # If the model added explanations, try to slice out the JSON object only.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

        data = json.loads(text)
        steps = data.get("steps") or fallback_steps

        # Ensure at least one screenshot action exists
        if not any(isinstance(s, dict) and s.get("action") == "screenshot" for s in steps):
            print("[route-diff] No screenshot step in LLM output, adding fallback screenshot", flush=True)
            steps.append({"action": "screenshot"})

        # Validate + normalize before handing off to executor
        validated = validate_steps(steps)
        if not validated:
            validated = fallback_steps
        normalized = normalize_steps(validated)
        if not normalized:
            normalized = fallback_steps

        narration = data.get("narration") or fallback_narration
        print(f"[route-diff] steps: {normalized}", flush=True)
        print(f"✅ [route-diff] Azure OpenAI returned {len(normalized)} steps", flush=True)
        return {"steps": normalized, "narration": narration}

    except Exception as e:
        print(f"❌ [route-diff] Azure step generation failed: {type(e).__name__}: {e}", flush=True)
        return {"steps": fallback_steps, "narration": fallback_narration, "budget_exceeded": False}


async def analyze_pr(
    repo_full_name: str,
    pr_number: int,
    pr_title: Optional[str],
    staging_url: str,
) -> Dict[str, Any]:
    """
    Main Phase 1 entrypoint.
    - Fetches real diff files
    - Calls LLM (stubbed for now) to generate steps + narration
    - Provides deterministic fallback on failure
    """
    try:
        print(f"🔍 [route-diff] Fetching PR diff for {repo_full_name}#{pr_number}...", flush=True)
        diff_files = fetch_pr_diff(repo_full_name, pr_number)

        if not diff_files:
            print("⚠️ [route-diff] No diff files, using default single screenshot", flush=True)
            return {
                "steps": [{"action": "screenshot"}],
                "narration": "Demo screenshot for this pull request.",
            }

        print(f"📁 [route-diff] Found {len(diff_files)} diff file(s)", flush=True)
        for f in diff_files:
            print(f"   - {f['path']} ({f['status']})", flush=True)

        flow = await generate_steps_from_diff(diff_files, pr_title, staging_url)
        steps = flow.get("steps") or [{"action": "screenshot"}]
        narration = flow.get("narration") or "Demo screenshot for this pull request."
        budget_exceeded = flow.get("budget_exceeded", False)

        print(f"✅ [route-diff] Generated {len(steps)} steps from diff", flush=True)
        return {"steps": steps, "narration": narration, "budget_exceeded": budget_exceeded}

    except Exception as e:
        print(f"❌ [route-diff] Error analyzing PR diff: {type(e).__name__}: {e}", flush=True)
        import traceback

        traceback.print_exc()
        # Deterministic fallback
        return {
            "steps": [{"action": "screenshot"}],
            "narration": "Demo screenshot for this pull request (fallback).",
        }
