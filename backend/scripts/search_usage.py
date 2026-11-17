"""Report Tavily credit usage without spending a search credit.

`GET /usage` is metadata, not retrieval: it does not consume the monthly
search allowance. Used as a pre-flight check before a batch of live search
verification, the same way the OpenRouter key endpoint is used for models.

    uv run python scripts/search_usage.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from app.adapters.search.tavily import TavilySearchProvider

#: Tavily's free plan allowance, for context in the printed output.
FREE_PLAN_CREDITS = 1000


def _line(label: str, value: Any) -> None:
    print(f"  {label:22} {value}")


async def main() -> int:
    api_key = os.environ.get("SEARCH_API_KEY", "").strip()
    if not api_key or api_key.startswith("__"):
        print("SEARCH_API_KEY is not set", file=sys.stderr)
        return 2

    provider = TavilySearchProvider(
        api_key=api_key,
        base_url=os.environ.get("SEARCH_BASE_URL", "https://api.tavily.com"),
        timeout_seconds=20.0,
        max_attempts=1,
    )
    try:
        payload = await provider.usage()
    except Exception as exc:
        print(f"could not read usage: {exc}", file=sys.stderr)
        return 1
    finally:
        await provider.aclose()

    account = payload.get("account") or {}
    key = payload.get("key") or {}

    print("Tavily usage (no search credit consumed)")
    _line("plan", account.get("current_plan", "unknown"))
    used = account.get("plan_usage")
    limit = account.get("plan_limit") or FREE_PLAN_CREDITS
    _line("plan_usage", used)
    _line("plan_limit", limit)
    if isinstance(used, int | float) and isinstance(limit, int | float) and limit:
        _line("remaining", f"{limit - used:.0f} of {limit:.0f}")
    _line("search_usage", account.get("search_usage", key.get("search_usage")))
    # paygo stays at zero without a payment method on file, which is what
    # makes overspend structurally impossible rather than merely unlikely.
    _line("paygo_usage", account.get("paygo_usage"))
    _line("paygo_limit", account.get("paygo_limit"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
