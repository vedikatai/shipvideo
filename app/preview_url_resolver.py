"""
Resolves preview URLs for PRs by querying GitHub commit statuses.

Important: Vercel commit statuses often point to the *dashboard* URL
(e.g., https://vercel.com/.../project/...), not the live app URL
(e.g., https://shipvideo-demo.vercel.app).

For this MVP, we normalize any Vercel status URL to the configured
app URL from project_config.json, instead of driving the dashboard.
"""
import os
import requests
import time
from app.config import load_config

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"


def _normalize_preview_url(target_url: str) -> str:
    """
    Normalize the preview URL.

    For Vercel, commit statuses usually give the dashboard URL.
    We instead use the app URL from config if available.
    """
    config = load_config()
    app_url = config.get("preview_url_template")

    # If app_url is configured, prefer it over the dashboard/status URL
    if app_url:
        print(f"ℹ️ Using configured app URL instead of status URL: {app_url}", flush=True)
        return app_url

    # Fallback: use the status target_url as-is
    return target_url


def resolve_preview_url_for_pr(pr_number: int, repo_name: str, timeout: int = 300) -> str:
    """
    Resolves the preview URL for a PR by querying GitHub commit statuses.
    
    Args:
        pr_number: The PR number
        repo_name: Repository name in format "owner/repo"
        timeout: Maximum seconds to wait for preview URL (default: 5 minutes)
    
    Returns:
        str: The preview URL (e.g., "https://yourapp-pr456.vercel.app")
    
    Raises:
        ValueError: If preview URL cannot be resolved
        Exception: If GitHub API call fails
    """
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN environment variable is required")
    
    # Get PR details to find head commit SHA
    pr_url = f"{GITHUB_API_BASE}/repos/{repo_name}/pulls/{pr_number}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        pr_response = requests.get(pr_url, headers=headers, timeout=10)
        pr_response.raise_for_status()
        pr_data = pr_response.json()
        commit_sha = pr_data["head"]["sha"]
        
        print(f"🔍 Found PR #{pr_number} head commit: {commit_sha[:7]}", flush=True)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to fetch PR #{pr_number} from GitHub: {e}")
    
    # Poll for commit statuses (Vercel posts status when deployment is ready)
    start_time = time.time()
    max_attempts = timeout // 10  # Check every 10 seconds
    
    for attempt in range(max_attempts):
        # Get commit statuses
        statuses_url = f"{GITHUB_API_BASE}/repos/{repo_name}/commits/{commit_sha}/statuses"
        
        try:
            statuses_response = requests.get(statuses_url, headers=headers, timeout=10)
            statuses_response.raise_for_status()
            statuses = statuses_response.json()
            
            # Look for Vercel preview status (most common)
            for status in statuses:
                context = status.get("context", "").lower()
                target_url = status.get("target_url", "")
                
                # Vercel posts status with context "vercel/preview" or "Vercel"
                if "vercel" in context and target_url:
                    print(f"✅ Found Vercel status URL: {target_url}", flush=True)
                    return _normalize_preview_url(target_url)
                
                # Netlify posts with context "netlify"
                if "netlify" in context and target_url:
                    print(f"✅ Found Netlify preview URL: {target_url}", flush=True)
                    # For Netlify, target_url is usually already the app URL
                    return target_url
            
            # If we haven't found it yet, wait and retry
            if attempt < max_attempts - 1:
                elapsed = time.time() - start_time
                print(f"⏳ Preview URL not found yet (attempt {attempt + 1}/{max_attempts}, {elapsed:.0f}s elapsed). Waiting...", flush=True)
                time.sleep(10)
        
        except requests.exceptions.RequestException as e:
            if attempt < max_attempts - 1:
                print(f"⚠️ GitHub API error (attempt {attempt + 1}): {e}. Retrying...", flush=True)
                time.sleep(10)
            else:
                raise Exception(f"Failed to fetch commit statuses from GitHub: {e}")
    
    # If we get here, we didn't find the preview URL
    elapsed = time.time() - start_time
    raise ValueError(
        f"Cannot resolve preview URL for PR #{pr_number}. "
        f"Deployment status not found on commit {commit_sha[:7]} after {elapsed:.0f}s. "
        f"Ensure the PR has been deployed (Vercel/Netlify) and deployment status is posted to GitHub."
    )
