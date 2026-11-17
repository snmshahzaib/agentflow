"""The rate-limiting port.

A public demo is a shared, metered resource: every task costs search credits
and model tokens that someone else cannot then use. Limiting request rate is
the cheapest control that protects the allowance, and it sits behind a port so
the API has no opinion about Redis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The outcome of one rate-limit check."""

    allowed: bool
    #: Requests already counted in the current window, including this one.
    used: int
    limit: int
    #: Whole seconds until the window resets. Sent as ``Retry-After``.
    retry_after_seconds: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class RateLimiter(Protocol):
    """Outbound port for request-rate accounting."""

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """Count one request against ``key`` and say whether it may proceed."""
        ...
