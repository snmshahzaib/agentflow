"""Logging guarantees.

Two project rules are enforced by processors rather than by discipline at call
sites, so they are tested directly: credentials never reach the log stream,
and every line can be correlated back to a request or task.
"""

from __future__ import annotations

import json

import pytest

from app.core.correlation import (
    get_correlation_id,
    new_correlation_id,
    reset_correlation_id,
    reset_task_id,
    set_correlation_id,
    set_task_id,
)
from app.core.logging import _add_correlation, _redact_sensitive, configure_logging, get_logger


class TestRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "llm_api_key",
            "LLM_API_KEY",
            "authorization",
            "Cookie",
            "password",
            "session_secret",
            "access_token",
            "search_api_key",
            "aws_credential",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        out = _redact_sensitive(None, "info", {key: "unit-test-credential-value"})
        assert out[key] == "[REDACTED]"
        assert "unit-test-credential-value" not in json.dumps(out)

    @pytest.mark.parametrize("key", ["task_id", "node", "model", "tokens_in", "duration_ms"])
    def test_ordinary_keys_pass_through(self, key: str) -> None:
        out = _redact_sensitive(None, "info", {key: "value"})
        assert out[key] == "value"

    def test_redaction_survives_a_real_log_call(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(level="info", fmt="json")
        get_logger("test").info(
            "provider_call", llm_api_key="unit-test-credential-value", model="vendor/m"
        )
        captured = capsys.readouterr().out
        assert "unit-test-credential-value" not in captured
        assert "[REDACTED]" in captured
        assert "vendor/m" in captured


class TestCorrelation:
    def test_correlation_id_is_attached_when_set(self) -> None:
        token = set_correlation_id("abc123")
        try:
            assert _add_correlation(None, "info", {})["correlation_id"] == "abc123"
        finally:
            reset_correlation_id(token)

    def test_absent_identifiers_are_omitted_rather_than_null(self) -> None:
        out = _add_correlation(None, "info", {})
        assert "correlation_id" not in out
        assert "task_id" not in out

    def test_task_id_is_attached_inside_a_job(self) -> None:
        ct = set_correlation_id("cid")
        tt = set_task_id("task-7")
        try:
            out = _add_correlation(None, "info", {})
            assert out == {"correlation_id": "cid", "task_id": "task-7"}
        finally:
            reset_task_id(tt)
            reset_correlation_id(ct)

    def test_an_explicit_value_is_not_overwritten(self) -> None:
        token = set_correlation_id("ambient")
        try:
            out = _add_correlation(None, "info", {"correlation_id": "explicit"})
            assert out["correlation_id"] == "explicit"
        finally:
            reset_correlation_id(token)

    def test_ids_are_unique_and_context_local(self) -> None:
        assert new_correlation_id() != new_correlation_id()
        assert get_correlation_id() is None


class TestConfiguration:
    @pytest.mark.parametrize("fmt", ["json", "console"])
    def test_both_renderers_configure_cleanly(
        self, fmt: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level="debug", fmt=fmt)
        get_logger("test").info("hello", field="value")
        assert "hello" in capsys.readouterr().out

    def test_json_output_is_parseable_and_carries_level_and_timestamp(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level="info", fmt="json")
        get_logger("test").warning("something_happened", count=3)
        line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert line["event"] == "something_happened"
        assert line["level"] == "warning"
        assert line["count"] == 3
        assert "timestamp" in line

    def test_level_filtering_suppresses_lower_levels(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level="warning", fmt="json")
        log = get_logger("test")
        log.info("suppressed")
        log.error("emitted")
        out = capsys.readouterr().out
        assert "suppressed" not in out
        assert "emitted" in out
