"""Paths and environment loading for the Bettor backend."""

from pathlib import Path

from dotenv import load_dotenv

# backend/ (parent of app/)
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def load_environment() -> None:
    """Load `.env` from repo root, then `backend/.env` (overrides)."""
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT / ".env", override=True)
