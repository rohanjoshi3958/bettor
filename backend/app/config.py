"""Paths and environment loading for the Bettor backend."""

from pathlib import Path

from dotenv import load_dotenv

# backend/ (parent of package `app/`)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

# Built UI (HTML, CSS, JS, images) lives beside `backend/`, not inside it.
STATIC = REPO_ROOT / "frontend"


def load_environment() -> None:
    """Load `.env` from repo root, then `backend/.env` (overrides)."""
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BACKEND_ROOT / ".env", override=True)
