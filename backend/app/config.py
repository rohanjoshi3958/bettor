"""Paths and environment loading for the Bettor backend."""

from pathlib import Path

from dotenv import load_dotenv

# backend/ (parent of package `app/`)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

# Local dev: `frontend/` is a sibling of `backend/`. Docker: copy `frontend/` into `BACKEND_ROOT/frontend`.
_static_bundled = BACKEND_ROOT / "frontend"
_static_repo_sibling = REPO_ROOT / "frontend"
STATIC = _static_bundled if _static_bundled.is_dir() else _static_repo_sibling


def load_environment() -> None:
    """Load `.env` from repo root, then `backend/.env` (overrides)."""
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BACKEND_ROOT / ".env", override=True)
