"""Paths and environment loading for the Bettor backend."""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ (parent of package `app/`)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

# Local dev: `frontend/` is a sibling of `backend/`. Docker: copy `frontend/` into `BACKEND_ROOT/frontend`.
_static_bundled = BACKEND_ROOT / "frontend"
_static_repo_sibling = REPO_ROOT / "frontend"
STATIC = _static_bundled if _static_bundled.is_dir() else _static_repo_sibling

# Origins allowed during local development when CORS_ALLOWED_ORIGINS is not set.
_DEFAULT_DEV_ORIGINS: list[str] = [
    "http://localhost:8000",
    "http://localhost:3000",
    "http://localhost:5173",
]


def get_cors_origins() -> list[str]:
    """Return the list of CORS-allowed origins.

    In production, set the ``CORS_ALLOWED_ORIGINS`` environment variable to a
    comma-separated list of approved frontend origins, e.g.::

        CORS_ALLOWED_ORIGINS=https://rohanjoshi.net

    When the variable is unset (local development), a set of common localhost
    origins is used so the dev server works without any manual configuration.
    Wildcard ``*`` origins are never emitted.

    Credentials (``allow_credentials=True``) are enabled so that browsers can
    send cookies or ``Authorization`` headers if needed in the future; this is
    safe because origins are always explicit rather than ``*``.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return list(_DEFAULT_DEV_ORIGINS)


def load_environment() -> None:
    """Load `.env` from repo root, then `backend/.env` (overrides)."""
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BACKEND_ROOT / ".env", override=True)
