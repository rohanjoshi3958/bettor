"""Tests for CORS origin allowlist configuration (BET-2).

CORS is enforced at the browser level via response headers.  These tests
drive a minimal FastAPI app wired with ``get_cors_origins()`` so that
monkeypatching ``CORS_ALLOWED_ORIGINS`` is reflected in the middleware
without re-importing the already-assembled ``app.main`` module.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

PRODUCTION_ORIGIN = "https://rohanjoshi.net"
LOCAL_ORIGIN_8000 = "http://localhost:8000"
LOCAL_ORIGIN_3000 = "http://localhost:3000"
LOCAL_ORIGIN_5173 = "http://localhost:5173"
UNAPPROVED_ORIGIN = "https://evil.example.com"


def _make_cors_client(monkeypatch: pytest.MonkeyPatch, *, cors_env: str | None) -> TestClient:
    """Build a minimal test app that reads CORS origins via ``get_cors_origins()``.

    Setting *cors_env* to a string sets ``CORS_ALLOWED_ORIGINS``; passing
    ``None`` removes it so the dev-default fallback is exercised.
    """
    if cors_env is not None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", cors_env)
    else:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    # Import after monkeypatching so get_cors_origins() sees the right env.
    from app.config import get_cors_origins

    probe_app = FastAPI()

    @probe_app.get("/probe")
    def probe() -> dict:
        return {"ok": True}

    probe_app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    return TestClient(probe_app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# get_cors_origins() unit tests
# ---------------------------------------------------------------------------


class TestGetCorsOrigins:
    def test_returns_production_origin_when_env_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", PRODUCTION_ORIGIN)
        from app.config import get_cors_origins

        assert get_cors_origins() == [PRODUCTION_ORIGIN]

    def test_supports_multiple_comma_separated_origins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        extra = "https://www.rohanjoshi.net"
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{PRODUCTION_ORIGIN},{extra}")
        from app.config import get_cors_origins

        assert get_cors_origins() == [PRODUCTION_ORIGIN, extra]

    def test_trims_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"  {PRODUCTION_ORIGIN}  ,  {LOCAL_ORIGIN_8000}  ")
        from app.config import get_cors_origins

        assert get_cors_origins() == [PRODUCTION_ORIGIN, LOCAL_ORIGIN_8000]

    def test_skips_blank_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{PRODUCTION_ORIGIN},,")
        from app.config import get_cors_origins

        assert get_cors_origins() == [PRODUCTION_ORIGIN]

    def test_falls_back_to_localhost_origins_when_env_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        from app.config import get_cors_origins

        origins = get_cors_origins()
        assert LOCAL_ORIGIN_8000 in origins
        assert LOCAL_ORIGIN_3000 in origins
        assert LOCAL_ORIGIN_5173 in origins

    def test_never_returns_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env in (PRODUCTION_ORIGIN, None):
            if env is not None:
                monkeypatch.setenv("CORS_ALLOWED_ORIGINS", env)
            else:
                monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
            from app.config import get_cors_origins

            assert "*" not in get_cors_origins()


# ---------------------------------------------------------------------------
# Production allowlist behaviour (HTTP-level)
# ---------------------------------------------------------------------------


class TestCORSProductionAllowlist:
    def test_production_origin_receives_acao_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_cors_client(monkeypatch, cors_env=PRODUCTION_ORIGIN)
        response = client.get("/probe", headers={"Origin": PRODUCTION_ORIGIN})
        assert response.headers.get("access-control-allow-origin") == PRODUCTION_ORIGIN

    def test_unapproved_origin_is_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_cors_client(monkeypatch, cors_env=PRODUCTION_ORIGIN)
        response = client.get("/probe", headers={"Origin": UNAPPROVED_ORIGIN})
        assert "access-control-allow-origin" not in response.headers

    def test_wildcard_is_not_emitted_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_cors_client(monkeypatch, cors_env=PRODUCTION_ORIGIN)
        response = client.get("/probe", headers={"Origin": PRODUCTION_ORIGIN})
        acao = response.headers.get("access-control-allow-origin", "")
        assert acao != "*"

    def test_each_of_multiple_production_origins_is_reflected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        www = "https://www.rohanjoshi.net"
        client = _make_cors_client(monkeypatch, cors_env=f"{PRODUCTION_ORIGIN},{www}")
        for origin in [PRODUCTION_ORIGIN, www]:
            resp = client.get("/probe", headers={"Origin": origin})
            assert resp.headers.get("access-control-allow-origin") == origin

    def test_preflight_request_for_production_origin_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_cors_client(monkeypatch, cors_env=PRODUCTION_ORIGIN)
        response = client.options(
            "/probe",
            headers={
                "Origin": PRODUCTION_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == PRODUCTION_ORIGIN

    def test_preflight_from_unapproved_origin_is_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_cors_client(monkeypatch, cors_env=PRODUCTION_ORIGIN)
        response = client.options(
            "/probe",
            headers={
                "Origin": UNAPPROVED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Development defaults
# ---------------------------------------------------------------------------


class TestCORSDevelopmentDefaults:
    @pytest.mark.parametrize("origin", [LOCAL_ORIGIN_8000, LOCAL_ORIGIN_3000, LOCAL_ORIGIN_5173])
    def test_default_localhost_origins_are_allowed(self, monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
        client = _make_cors_client(monkeypatch, cors_env=None)
        response = client.get("/probe", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == origin

    def test_unapproved_origin_is_excluded_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_cors_client(monkeypatch, cors_env=None)
        response = client.get("/probe", headers={"Origin": UNAPPROVED_ORIGIN})
        assert "access-control-allow-origin" not in response.headers

    def test_production_origin_is_not_in_dev_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The production domain must be opted in explicitly via CORS_ALLOWED_ORIGINS."""
        client = _make_cors_client(monkeypatch, cors_env=None)
        response = client.get("/probe", headers={"Origin": PRODUCTION_ORIGIN})
        assert "access-control-allow-origin" not in response.headers
