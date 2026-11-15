"""Cost estimation from catalogue pricing.

Rates are never hardcoded: they come from the provider catalogue at runtime,
because published prices change and a constant in the source would quietly
rot. Where the gateway reports an authoritative per-generation cost we store
that too — the two can legitimately diverge, and pretending otherwise would
make the cost ledger dishonest.
"""

from __future__ import annotations

from app.adapters.llm.base import ModelCapabilities


def estimate_cost_usd(
    caps: ModelCapabilities,
    *,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
) -> float | None:
    """Estimate the USD cost of one call, or ``None`` if pricing is unknown.

    Cached prompt tokens are billed at a reduced rate that catalogues do not
    expose uniformly, so they are counted at the full prompt rate: an estimate
    that errs high is safer for a budget ceiling than one that errs low.
    """
    if caps.prompt_usd_per_token < 0 or caps.completion_usd_per_token < 0:
        return None
    billable_in = max(tokens_in, 0) + max(cache_read_tokens, 0)
    return (
        billable_in * caps.prompt_usd_per_token + max(tokens_out, 0) * caps.completion_usd_per_token
    )


def estimate_task_cost_usd(
    caps: ModelCapabilities, *, tokens_in: int, tokens_out: int, calls: int
) -> float | None:
    """Projected cost of ``calls`` identical calls. Used for budget headroom."""
    per_call = estimate_cost_usd(caps, tokens_in=tokens_in, tokens_out=tokens_out)
    return None if per_call is None else per_call * max(calls, 0)
