"""
LLM cost and safety guards. Change limits and behavior here without touching pipeline logic.

- max_tokens: cap Azure response size
- budget: pause LLM spend above a dollar limit (internal tracking, or from Azure Cost Management)
- dedupe: skip re-running for the same PR+commit (redeliveries)
- skip_llm_for_size: skip LLM when diff payload is too large (use fallback steps)

Optional: set AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
to use Azure Cost Management API for real month-to-date spend (then we compare to BUDGET_LIMIT_USD).
Optional: set AZURE_BILLING_ACCOUNT_ID to fetch current credit balance (Consumption Balances API).
When Azure is configured, expenditure/remaining/budget come from Azure only; we do not write to the local spend file.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None  # type: ignore

# -----------------------------------------------------------------------------
# Config (change these for different environments / requirements)
# -----------------------------------------------------------------------------
MAX_RESPONSE_TOKENS = 500
"""Cap on Azure response tokens so a single response can't be huge."""

BUDGET_LIMIT_USD = 15.0
"""Budget cap: when Azure month-to-date spend exceeds this, we skip LLM. Period: per calendar month (we use MonthToDate)."""

# Azure Cost Management returns spend in your subscription's billing currency (USD, INR, etc.).
# Set BUDGET_LIMIT_USD to a value in the same currency Azure returns (e.g. 15 for $15 or 1500 for ₹1500).

MAX_DIFF_CHARS_TO_SKIP_LLM = 12_000
"""If diff payload (after our normal truncation) exceeds this, skip LLM and use fallback."""

# Approximate gpt-4o-mini pricing per 1K tokens (adjust if Azure pricing changes)
PRICE_PER_1K_INPUT_USD = 0.000_15
PRICE_PER_1K_OUTPUT_USD = 0.000_6

DATA_DIR = Path(__file__).resolve().parent / "data"
SPEND_FILE = DATA_DIR / "llm_spend.json"
DEDUPE_FILE = DATA_DIR / "llm_dedup.json"
MAX_DEDUPE_ENTRIES = 500

_lock = threading.Lock()


def _azure_configured() -> bool:
    """True if Azure Cost Management env vars are set (Azure is source of truth for spend)."""
    return bool(
        os.getenv("AZURE_SUBSCRIPTION_ID")
        and os.getenv("AZURE_TENANT_ID")
        and os.getenv("AZURE_CLIENT_ID")
        and os.getenv("AZURE_CLIENT_SECRET")
    )


# Optional: cache Azure spend and credit balance to avoid hitting the API every check
_azure_spend_cache: float | None = None
_azure_spend_cache_ts: float = 0
_azure_balance_cache: dict | None = None
_azure_balance_cache_ts: float = 0
AZURE_SPEND_CACHE_SECONDS = 300  # 5 min


def _get_azure_token() -> str | None:
    """Get Azure AD token for management.azure.com. Returns None if not configured or on error."""
    tenant = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    if not all([tenant, client_id, client_secret]) or requests is None:
        return None
    try:
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        token_resp = requests.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://management.azure.com/.default",
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        token_resp.raise_for_status()
        return token_resp.json().get("access_token")
    except Exception:
        return None


def fetch_azure_cost_month_to_date() -> float | None:
    """
    Fetch current month-to-date spend (USD) from Azure Cost Management API.
    Requires env: AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET.
    Returns None if not configured or on error.
    """
    global _azure_spend_cache, _azure_spend_cache_ts
    import time as _time
    now = _time.time()
    if _azure_spend_cache is not None and (now - _azure_spend_cache_ts) < AZURE_SPEND_CACHE_SECONDS:
        return _azure_spend_cache

    sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
    if not sub_id:
        return None
    token = _get_azure_token()
    if not token:
        return None

    try:
        query_url = f"https://management.azure.com/subscriptions/{sub_id}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        query_body = {
            "type": "ActualCost",
            "timeframe": "MonthToDate",
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            },
        }
        cost_resp = requests.post(
            query_url,
            json=query_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )
        cost_resp.raise_for_status()
        data = cost_resp.json()
        props = data.get("properties") or {}
        rows = props.get("rows") or []
        if not rows or not rows[0]:
            _azure_spend_cache = 0.0
        else:
            # First column is cost in subscription billing currency (USD, INR, etc.)
            _azure_spend_cache = float(rows[0][0])
        _azure_spend_cache_ts = now
        return _azure_spend_cache
    except Exception as e:
        print(f"[llm-guards] Azure Cost Management fetch failed: {type(e).__name__}: {e}", flush=True)
        return None


def fetch_azure_credit_balance() -> dict | None:
    """
    Fetch current Azure credit balance (Microsoft Customer Agreement / billing account).
    Requires env: AZURE_BILLING_ACCOUNT_ID plus AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET.
    Returns None if not configured or on error. Response includes balance, currency, utilized, etc.
    """
    global _azure_balance_cache, _azure_balance_cache_ts
    import time as _time
    now = _time.time()
    if _azure_balance_cache is not None and (now - _azure_balance_cache_ts) < AZURE_SPEND_CACHE_SECONDS:
        return _azure_balance_cache

    billing_account_id = os.getenv("AZURE_BILLING_ACCOUNT_ID")
    if not billing_account_id:
        return None
    token = _get_azure_token()
    if not token or requests is None:
        return None

    try:
        url = (
            f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/{billing_account_id}"
            f"/providers/Microsoft.Consumption/balances?api-version=2023-05-01"
        )
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Response can be a single object or value array; docs show single balance object
        props = data.get("properties") or data
        if isinstance(data.get("value"), list) and len(data["value"]) > 0:
            props = data["value"][0].get("properties") or data["value"][0]
        ending = float(props.get("endingBalance", 0) or 0)
        beginning = float(props.get("beginningBalance", 0) or 0)
        utilized = float(props.get("totalUsage", 0) or props.get("utilized", 0) or 0)
        currency = str(props.get("currency", "USD") or "USD")
        _azure_balance_cache = {
            "credit_balance": ending,
            "currency": currency,
            "beginning_balance": beginning,
            "utilized": utilized,
        }
        _azure_balance_cache_ts = now
        return _azure_balance_cache
    except Exception as e:
        print(f"[llm-guards] Azure credit balance fetch failed: {type(e).__name__}: {e}", flush=True)
        return None


def get_budget_status() -> dict:
    """
    Return current spend, limit, and optional credit balance. Prefers Azure Cost Management
    when configured; otherwise uses local spend file. When AZURE_BILLING_ACCOUNT_ID is set,
    includes current Azure credit balance.
    """
    spend_azure = fetch_azure_cost_month_to_date()
    balance_info = fetch_azure_credit_balance()
    result: dict = {}
    if spend_azure is not None:
        result = {"current_spend_usd": spend_azure, "limit_usd": BUDGET_LIMIT_USD, "source": "azure"}
    else:
        _ensure_data_dir()
        total = 0.0
        if SPEND_FILE.exists():
            try:
                with open(SPEND_FILE) as f:
                    total = float(json.load(f).get("total_usd", 0))
            except Exception:
                pass
        result = {"current_spend_usd": total, "limit_usd": BUDGET_LIMIT_USD, "source": "local"}
    if balance_info is not None:
        result["credit_balance"] = balance_info["credit_balance"]
        result["credit_balance_currency"] = balance_info["currency"]
        result["credit_utilized"] = balance_info.get("utilized")
    return result


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_max_tokens() -> int:
    """Max tokens to allow for Azure chat completion response."""
    return MAX_RESPONSE_TOKENS


def check_budget() -> bool:
    """
    True if under budget (ok to call LLM). False if over budget (use fallback, don't call LLM).
    Uses Azure Cost Management month-to-date spend when env vars are set; otherwise local spend file.
    """
    spend_azure = fetch_azure_cost_month_to_date()
    if spend_azure is not None:
        print(f"[llm-guards] Azure MTD spend: ${spend_azure:.2f} / ${BUDGET_LIMIT_USD} limit", flush=True)
        if spend_azure >= BUDGET_LIMIT_USD:
            print(f"[llm-guards] Budget exceeded, skipping LLM", flush=True)
            return False
        return True
    _ensure_data_dir()
    with _lock:
        if not SPEND_FILE.exists():
            return True
        try:
            with open(SPEND_FILE) as f:
                data = json.load(f)
            total = float(data.get("total_usd", 0))
            if total >= BUDGET_LIMIT_USD:
                print(f"[llm-guards] Budget exceeded (local ${total:.2f} >= ${BUDGET_LIMIT_USD}), skipping LLM", flush=True)
                return False
            return True
        except Exception as e:
            print(f"[llm-guards] Could not read spend file: {e}", flush=True)
            return True


def record_spend(prompt_tokens: int, completion_tokens: int) -> None:
    """
    Record estimated cost from a single LLM call.
    When Azure Cost Management is configured, we do not update the local spend file;
    expenditure, remaining balance, and budget come from Azure only.
    When Azure is not configured, we update the local file for check_budget() / get_budget_status().
    """
    estimated = (prompt_tokens / 1000.0) * PRICE_PER_1K_INPUT_USD + (completion_tokens / 1000.0) * PRICE_PER_1K_OUTPUT_USD
    if _azure_configured():
        print(f"[llm-guards] LLM cost ~${estimated:.4f} (spend/balance from Azure, not recording locally)", flush=True)
        return
    _ensure_data_dir()
    with _lock:
        total = 0.0
        if SPEND_FILE.exists():
            try:
                with open(SPEND_FILE) as f:
                    total = float(json.load(f).get("total_usd", 0))
            except Exception:
                pass
        total += estimated
        with open(SPEND_FILE, "w") as f:
            json.dump({"total_usd": round(total, 4), "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z"}, f)
        print(f"[llm-guards] Recorded ~${estimated:.4f}; total ~${total:.2f}", flush=True)


def check_already_ran(repo: str, pr_number: int, commit_sha: str) -> bool:
    """True if we already ran for this repo + PR + commit (e.g. redelivery)."""
    if not commit_sha:
        return False
    key = f"{repo}#{pr_number}#{commit_sha}"
    _ensure_data_dir()
    with _lock:
        if not DEDUPE_FILE.exists():
            return False
        try:
            with open(DEDUPE_FILE) as f:
                data = json.load(f)
            if key in data.get("runs", {}):
                print(f"[llm-guards] Dedupe: already ran for {repo}#{pr_number} @ {commit_sha[:7]}", flush=True)
                return True
            return False
        except Exception:
            return False


def record_run(repo: str, pr_number: int, commit_sha: str) -> None:
    """Mark that we ran for this repo + PR + commit (call at start of pipeline for this PR)."""
    if not commit_sha:
        return
    key = f"{repo}#{pr_number}#{commit_sha}"
    _ensure_data_dir()
    with _lock:
        runs = {}
        if DEDUPE_FILE.exists():
            try:
                with open(DEDUPE_FILE) as f:
                    runs = json.load(f).get("runs", {})
            except Exception:
                pass
        runs[key] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
        if len(runs) > MAX_DEDUPE_ENTRIES:
            by_time = sorted(runs.items(), key=lambda x: x[1])
            runs = dict(by_time[-MAX_DEDUPE_ENTRIES:])
        with open(DEDUPE_FILE, "w") as f:
            json.dump({"runs": runs}, f)


def should_skip_llm_for_size(diff_char_count: int) -> bool:
    """True if diff payload is too large; use fallback steps instead of calling LLM."""
    if diff_char_count > MAX_DIFF_CHARS_TO_SKIP_LLM:
        print(f"[llm-guards] Diff size {diff_char_count} > {MAX_DIFF_CHARS_TO_SKIP_LLM}, skipping LLM", flush=True)
        return True
    return False
