"""Domain exception hierarchy.

Every failure the application raises deliberately is one of these. HTTP
mapping lives in ``app.api.errors``; nothing here knows about HTTP, and
nothing here imports a framework.

``code`` is a stable, machine-readable string that appears in the API error
envelope and in logs. Clients may branch on it; they may not branch on
``message``.
"""

from __future__ import annotations

from typing import Any


class AgentFlowError(Exception):
    """Base class for every deliberate failure."""

    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.message
        self.details: dict[str, Any] = {k: v for k, v in details.items() if v is not None}
        super().__init__(self.message)


# --- configuration -------------------------------------------------------


class ConfigurationError(AgentFlowError):
    """Invalid or incomplete configuration. Raised at startup, never per-request."""

    code = "configuration_error"
    message = "The application is misconfigured."


class ModelNotAvailableError(ConfigurationError):
    """A configured model is absent from the provider catalogue."""

    code = "model_not_available"
    message = "The configured model is not available from the provider."


class ModelCapabilityError(ConfigurationError):
    """A configured model cannot satisfy a requirement, such as schema output."""

    code = "model_capability_unmet"
    message = "The configured model does not meet AgentFlow's requirements."


# --- request / state -----------------------------------------------------


class NotFoundError(AgentFlowError):
    code = "not_found"
    message = "The requested resource does not exist."


class ValidationError(AgentFlowError):
    code = "validation_error"
    message = "The request was not valid."


class ConflictError(AgentFlowError):
    """The request is valid but the resource is in an incompatible state."""

    code = "conflict"
    message = "The resource is not in a state that permits this operation."


class ApprovalRequiredError(ConflictError):
    code = "approval_required"
    message = "This report has not been approved and cannot be exported."


class SearchUnavailableError(AgentFlowError):
    """Retrieval produced nothing, so there is no evidence to reason over.

    Distinct from an empty result set: that means the provider answered and
    found nothing, which is a finding. This means retrieval itself failed.
    """

    code = "search_unavailable"
    message = "Retrieval failed and no evidence was gathered."


class UnsafeUrlError(ValidationError):
    code = "unsafe_url"
    message = "The URL was rejected by the safety validator."


# --- budgets and rate limits --------------------------------------------


class BudgetExhaustedError(AgentFlowError):
    """A per-task token or call budget was consumed. Fails the task cleanly."""

    code = "budget_exhausted"
    message = "The task exhausted its model budget."


class RateLimitedError(AgentFlowError):
    code = "rate_limited"
    message = "Too many requests."


class DemoBudgetExceededError(RateLimitedError):
    code = "demo_budget_exceeded"
    message = "The daily demo budget has been spent."


class DemoTaskLimitError(RateLimitedError):
    """The deployment-wide daily task ceiling is reached."""

    code = "demo_daily_task_limit"
    message = "The demo has reached its daily task limit."


class SessionTaskLimitError(RateLimitedError):
    """One visitor has opened more tasks than a shared demo allows."""

    code = "session_task_limit"
    message = "This session has reached its task limit."


class QueueUnavailableError(AgentFlowError):
    """The work queue could not be reached.

    Raised rather than allowed to surface as an unhandled error, because the
    difference matters to a caller: a 503 says "the system is degraded, retry
    later", while a 500 says "we have a bug". It also keeps the broker's own
    exception text -- which names hosts and ports -- out of the response.
    """

    code = "queue_unavailable"
    message = "The work queue is unavailable. The task was not started; please retry."


class AccessDeniedError(AgentFlowError):
    """A gated capability was requested without the right credential.

    Deliberately *not* a 401: there is no identity to authenticate here and no
    login to redirect to. It is a refusal to perform a privileged action, and
    the message never says whether a code was wrong or merely absent.
    """

    code = "access_denied"
    message = "Live research is not available without an access code."


# --- outbound adapters ---------------------------------------------------


class AdapterError(AgentFlowError):
    """Base class for outbound provider failures."""

    code = "adapter_error"
    message = "An external provider call failed."
    #: Whether retrying the same call could plausibly succeed.
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: float | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, **details)
        #: How long the provider asked us to wait, when it said so. Honouring
        #: this matters on rate-limited free tiers, where an exponential
        #: backoff guess is usually wrong in both directions.
        self.retry_after_seconds = retry_after_seconds


class ProviderTimeoutError(AdapterError):
    code = "provider_timeout"
    message = "The provider did not respond in time."
    retryable = True


class ProviderUnavailableError(AdapterError):
    code = "provider_unavailable"
    message = "The provider is temporarily unavailable."
    retryable = True


class ProviderRateLimitedError(AdapterError):
    code = "provider_rate_limited"
    message = "The provider rate-limited the request."
    retryable = True


class ProviderAuthError(AdapterError):
    code = "provider_auth_failed"
    message = "The provider rejected the credentials."
    retryable = False


class SchemaValidationError(AdapterError):
    """The model returned output that does not satisfy the requested schema."""

    code = "schema_validation_failed"
    message = "The model returned output that did not match the required schema."
    retryable = True
