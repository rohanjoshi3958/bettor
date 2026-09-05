"""Tests for structured application logging (BET-8)."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from app.logging_config import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    get_log_format,
    get_log_level,
    log_event,
    request_id_var,
    sanitize_log_extra,
)


class TestSanitizeLogExtra:
    def test_redacts_api_key(self) -> None:
        out = sanitize_log_extra({"api_key": "secret-value", "sport_key": "basketball_nba"})
        assert out["api_key"] == "[redacted]"
        assert out["sport_key"] == "basketball_nba"

    def test_redacts_nested_authorization(self) -> None:
        out = sanitize_log_extra({"headers": {"Authorization": "Bearer xyz", "Accept": "json"}})
        assert out["headers"]["Authorization"] == "[redacted]"
        assert out["headers"]["Accept"] == "json"

    def test_redacts_token_and_password_keys(self) -> None:
        out = sanitize_log_extra({"access_token": "t", "password": "p", "ok": 1})
        assert out["access_token"] == "[redacted]"
        assert out["password"] == "[redacted]"
        assert out["ok"] == 1


class TestLogFormatEnv:
    def test_default_text_locally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("ENV", raising=False)
        assert get_log_format() == "text"

    def test_json_on_render(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        monkeypatch.setenv("RENDER", "true")
        assert get_log_format() == "json"

    def test_json_in_production_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert get_log_format() == "json"

    def test_explicit_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RENDER", "true")
        monkeypatch.setenv("LOG_FORMAT", "text")
        assert get_log_format() == "text"

    def test_log_level_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert get_log_level() == "INFO"
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert get_log_level() == "DEBUG"


class TestJsonFormatter:
    def test_emits_parseable_json_with_extras(self) -> None:
        record = logging.LogRecord(
            name="bettor.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.sport_key = "basketball_nba"  # type: ignore[attr-defined]
        record.api_key = "should-not-leak"  # type: ignore[attr-defined]
        line = JsonFormatter().format(record)
        payload = json.loads(line)
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "bettor.test"
        assert payload["sport_key"] == "basketball_nba"
        assert payload["api_key"] == "[redacted]"
        assert "timestamp" in payload

    def test_includes_request_id_from_context(self) -> None:
        configure_logging(force=True)
        token = request_id_var.set("req-abc")
        try:
            stream = StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonFormatter())
            from app.logging_config import _ContextFilter

            handler.addFilter(_ContextFilter())
            log = logging.getLogger("bettor.test.ctx")
            log.handlers.clear()
            log.addHandler(handler)
            log.setLevel(logging.INFO)
            log.propagate = False
            log.info("ping")
            payload = json.loads(stream.getvalue().strip())
            assert payload["request_id"] == "req-abc"
            assert payload["message"] == "ping"
        finally:
            request_id_var.reset(token)
            log.handlers.clear()


class TestTextFormatter:
    def test_includes_extras_and_redacts(self) -> None:
        record = logging.LogRecord(
            name="bettor.test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="warn",
            args=(),
            exc_info=None,
        )
        record.token = "secret"  # type: ignore[attr-defined]
        record.path = "/api/picks"  # type: ignore[attr-defined]
        line = TextFormatter(datefmt="%Y-%m-%dT%H:%M:%S").format(record)
        assert "warn" in line
        assert "[redacted]" in line
        assert "secret" not in line
        assert "/api/picks" in line


class TestLogEvent:
    def test_log_event_redacts_secrets(self) -> None:
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        log = logging.getLogger("bettor.test.event")
        log.handlers.clear()
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        try:
            log_event(log, logging.INFO, "evt", apiKey="xyz", sport_key="nba")
            payload = json.loads(stream.getvalue().strip())
            assert payload["message"] == "evt"
            assert payload["apiKey"] == "[redacted]"
            assert payload["sport_key"] == "nba"
        finally:
            log.handlers.clear()


class TestRequestLoggingMiddleware:
    def test_adds_request_id_and_echoes_incoming(self, client) -> None:
        response = client.get("/api/health", headers={"X-Request-ID": "fixed-id-123"})
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == "fixed-id-123"

    def test_generates_request_id_when_absent(self, client) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        rid = response.headers.get("X-Request-ID")
        assert rid
        assert len(rid) >= 8

    def test_logs_request_completed_as_json(
        self, client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_FORMAT", "json")
        configure_logging(force=True)
        client.get("/api/health")
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "request_completed" in ln]
        assert lines, f"expected request_completed in stdout, got: {out!r}"
        payload = json.loads(lines[-1])
        assert payload["message"] == "request_completed"
        assert payload["method"] == "GET"
        assert payload["path"] == "/api/health"
        assert payload["status_code"] == 200
        assert "duration_ms" in payload
        assert "request_id" in payload
        assert "api_key" not in payload
        assert "apiKey" not in payload
