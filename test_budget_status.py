#!/usr/bin/env python3
"""
Test Azure Cost Management / budget tracking.
Loads .env from project root, then calls get_budget_status() and prints result.

Run from project root:
  python test_budget_status.py
"""
import os
import sys
from pathlib import Path

# Project root (parent of script if script is in repo root)
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def load_dotenv():
    if not ENV_FILE.exists():
        print(f"No .env at {ENV_FILE}", file=sys.stderr)
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                os.environ.setdefault(k, v)


def main():
    load_dotenv()

    required = ["AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"Missing env: {missing}")
        print("Set them in .env to test Azure Cost Management.")
        sys.exit(1)

    sys.path.insert(0, str(ROOT))
    from app.llm_guards import get_budget_status, fetch_azure_cost_month_to_date, BUDGET_LIMIT_USD

    print("Fetching Azure Cost Management (month-to-date spend)...")
    spend = fetch_azure_cost_month_to_date()
    if spend is None:
        print("Azure fetch returned None (check role: Cost Management Reader on subscription).")
        status = get_budget_status()
        print(f"Fallback status: {status}")
        sys.exit(2)

    status = get_budget_status()
    spend = status["current_spend_usd"]
    limit = status["limit_usd"]
    remaining = max(0.0, limit - spend)

    print("--- Expenditure / remaining / budget (from Azure) ---")
    print(f"Source: {status['source']}")
    print(f"Expenditure (MTD): ${spend:.2f}")
    print(f"Budget limit:      ${limit:.2f}")
    print(f"Remaining:        ${remaining:.2f}")
    print(f"Under budget:     {spend < limit}")

    if "credit_balance" in status:
        bal = status["credit_balance"]
        curr = status.get("credit_balance_currency", "USD")
        print("--- Azure credit balance ---")
        print(f"Current balance:   {curr} {bal:.2f}")
        if status.get("credit_utilized") is not None:
            print(f"Utilized:          {curr} {status['credit_utilized']:.2f}")
    else:
        print("--- Azure credit balance ---")
        print("(Set AZURE_BILLING_ACCOUNT_ID in .env to fetch credit balance)")


if __name__ == "__main__":
    main()
