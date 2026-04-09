from __future__ import annotations

import json
import os
import threading
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None                




MAX_RESPONSE_TOKENS = 500

BILLING_CURRENCY = os.getenv("BILLING_CURRENCY", "INR").strip().upper() or "INR"


def _resolve_budget_limit() -> float:
    if os.getenv("BUDGET_LIMIT") is not None and os.getenv("BUDGET_LIMIT", "").strip() != "":
        return float(os.getenv("BUDGET_LIMIT", "0"))
    if os.getenv("BUDGET_LIMIT_USD") is not None and os.getenv("BUDGET_LIMIT_USD", "").strip() != "":
        return float(os.getenv("BUDGET_LIMIT_USD", "0"))

    if BILLING_CURRENCY == "INR":
        return 1500.0
    return 15.0


BUDGET_LIMIT = _resolve_budget_limit()


BUDGET_LIMIT_USD = BUDGET_LIMIT

MAX_DIFF_CHARS_TO_SKIP_LLM = 12_000



PRICE_PER_1K_INPUT = float(os.getenv("PRICE_PER_1K_INPUT", "0.00015"))
PRICE_PER_1K_OUTPUT = float(os.getenv("PRICE_PER_1K_OUTPUT", "0.0006"))
PRICE_PER_1K_INPUT_USD = PRICE_PER_1K_INPUT                
PRICE_PER_1K_OUTPUT_USD = PRICE_PER_1K_OUTPUT

DATA_DIR = Path(__file__).resolve().parent / "data"
SPEND_FILE = DATA_DIR / "llm_spend.json"
DEDUPE_FILE = DATA_DIR / "llm_dedup.json"
MAX_DEDUPE_ENTRIES = 500




DEDUPE_ENABLED = os.getenv("LLM_DEDUPE_ENABLED", "true").lower() not in {"false", "0", "no"}

_lock = threading.Lock()


def format_currency_amount(amount: float, currency: str | None = None) -> str:
    cur = (currency or BILLING_CURRENCY).upper()
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(cur, f"{cur} ")

    if abs(amount) < 10:
        return f"{sym}{amount:.4f}"
    return f"{sym}{amount:.2f}"


def _azure_configured() -> bool:
    return bool(
        os.getenv("AZURE_SUBSCRIPTION_ID")
        and os.getenv("AZURE_TENANT_ID")
        and os.getenv("AZURE_CLIENT_ID")
        and os.getenv("AZURE_CLIENT_SECRET")
    )



_azure_spend_cache: float | None = None
_azure_spend_cache_ts: float = 0
_azure_balance_cache: dict | None = None
_azure_balance_cache_ts: float = 0
AZURE_SPEND_CACHE_SECONDS = 300         


def _get_azure_token() -> str | None:
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

            _azure_spend_cache = float(rows[0][0])
        _azure_spend_cache_ts = now
        return _azure_spend_cache
    except Exception as e:
        print(f"[llm-guards] Azure Cost Management fetch failed: {type(e).__name__}: {e}", flush=True)
        return None


def fetch_azure_credit_balance() -> dict | None:
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
    spend_azure = fetch_azure_cost_month_to_date()
    balance_info = fetch_azure_credit_balance()
    result: dict = {}
    if spend_azure is not None:
        result = {
            "currency": BILLING_CURRENCY,
            "current_spend": spend_azure,
            "budget_limit": BUDGET_LIMIT,
            "current_spend_usd": spend_azure,
            "limit_usd": BUDGET_LIMIT,
            "source": "azure",
        }
    else:
        _ensure_data_dir()
        total = 0.0
        if SPEND_FILE.exists():
            try:
                with open(SPEND_FILE) as f:
                    data = json.load(f)
                    total = float(data.get("total", data.get("total_usd", 0)))
            except Exception:
                pass
        result = {
            "currency": BILLING_CURRENCY,
            "current_spend": total,
            "budget_limit": BUDGET_LIMIT,
            "current_spend_usd": total,
            "limit_usd": BUDGET_LIMIT,
            "source": "local",
        }
    if balance_info is not None:
        result["credit_balance"] = balance_info["credit_balance"]
        result["credit_balance_currency"] = balance_info["currency"]
        result["credit_utilized"] = balance_info.get("utilized")
    return result


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_max_completion_tokens() -> int:
    return MAX_RESPONSE_TOKENS


def check_budget() -> bool:
    spend_azure = fetch_azure_cost_month_to_date()
    if spend_azure is not None:
        print(
            f"[llm-guards] Azure MTD spend: {format_currency_amount(spend_azure)} / "
            f"{format_currency_amount(BUDGET_LIMIT)} limit ({BILLING_CURRENCY})",
            flush=True,
        )
        if spend_azure >= BUDGET_LIMIT:
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
            total = float(data.get("total", data.get("total_usd", 0)))
            if total >= BUDGET_LIMIT:
                print(
                    f"[llm-guards] Budget exceeded (local {format_currency_amount(total)} >= "
                    f"{format_currency_amount(BUDGET_LIMIT)}), skipping LLM",
                    flush=True,
                )
                return False
            return True
        except Exception as e:
            print(f"[llm-guards] Could not read spend file: {e}", flush=True)
            return True


def estimate_run_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens / 1000.0) * PRICE_PER_1K_INPUT + (completion_tokens / 1000.0) * PRICE_PER_1K_OUTPUT


def record_spend(prompt_tokens: int, completion_tokens: int) -> None:
    estimated = estimate_run_cost(prompt_tokens, completion_tokens)
    if _azure_configured():
        print(
            f"[llm-guards] LLM cost est. ~{format_currency_amount(estimated)} "
            f"({BILLING_CURRENCY}; MTD from Azure, not recording locally)",
            flush=True,
        )
        return
    _ensure_data_dir()
    with _lock:
        total = 0.0
        if SPEND_FILE.exists():
            try:
                with open(SPEND_FILE) as f:
                    raw = json.load(f)
                    total = float(raw.get("total", raw.get("total_usd", 0)))
            except Exception:
                pass
        total += estimated
        with open(SPEND_FILE, "w") as f:
            json.dump(
                {
                    "total": round(total, 4),
                    "total_usd": round(total, 4),
                    "currency": BILLING_CURRENCY,
                    "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                },
                f,
            )
        print(
            f"[llm-guards] Recorded ~{format_currency_amount(estimated)}; "
            f"total ~{format_currency_amount(total)} ({BILLING_CURRENCY})",
            flush=True,
        )


def check_already_ran(repo: str, pr_number: int, commit_sha: str) -> bool:
    if not DEDUPE_ENABLED:
        print("[llm-guards] Dedupe disabled via LLM_DEDUPE_ENABLED, always running", flush=True)
        return False
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
    if diff_char_count > MAX_DIFF_CHARS_TO_SKIP_LLM:
        print(f"[llm-guards] Diff size {diff_char_count} > {MAX_DIFF_CHARS_TO_SKIP_LLM}, skipping LLM", flush=True)
        return True
    return False
