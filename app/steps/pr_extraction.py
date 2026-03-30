"""
PR extraction: fetch changed files and unified diffs for a pull request from GitHub.

Single responsibility: call GitHub REST API and return a list of file entries
(path, status, patch). Used by step generation to ground LLM prompts in real code changes.
"""
from __future__ import annotations

import os
from typing import List, Dict

import requests

from observability import pipeline_step


MAX_PATCH_CHARS = 3000
TRUNCATION_SUFFIX = "\n... (truncated)"
PER_PAGE = 100
MAX_PAGES = 50


@pipeline_step("pr_extraction")
def fetch_pr_diff(repo_full_name: str, pr_number: int) -> List[Dict[str, str]]:
    """
    Fetches changed files and their diffs for a PR using the GitHub REST API.

    Args:
        repo_full_name: Repository in "owner/repo" form.
        pr_number: Pull request number.

    Returns:
        List of dicts, each with:
          - path: str (e.g. "app/pricing/page.tsx")
          - status: str ("added" | "modified" | "removed" | "renamed")
          - patch: str (unified diff, truncated to MAX_PATCH_CHARS per file)
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN not set")

    result: List[Dict[str, str]] = []
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files"
    headers = {"Authorization": f"token {token}"}

    page = 1
    while page <= MAX_PAGES:
        resp = requests.get(
            url,
            headers=headers,
            params={"page": page, "per_page": PER_PAGE},
            timeout=30,
        )
        resp.raise_for_status()
        files = resp.json()
        if not files:
            break

        for f in files:
            raw_patch = f.get("patch") or ""
            patch = raw_patch[:MAX_PATCH_CHARS]
            if len(raw_patch) > MAX_PATCH_CHARS:
                patch = patch + TRUNCATION_SUFFIX
            result.append({
                "path": f["filename"],
                "status": f["status"],
                "patch": patch,
            })

        if len(files) < PER_PAGE:
            break
        page += 1

    if page > MAX_PAGES:
        print(f"[steps.pr_extraction] stopped at max_pages={MAX_PAGES}", flush=True)
    print(f"[steps.pr_extraction] files_changed={len(result)}", flush=True)
    return result
