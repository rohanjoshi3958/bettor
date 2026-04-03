"""
ASGI entrypoint — run from the `backend/` directory:

    uvicorn main:app --reload

Static UI is served from `../frontend/` (mounted at `/assets`, HTML at `/`).
Set THE_ODDS_API_KEY for live odds (the-odds-api.com).
"""

from app.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="localhost", port=8000, reload=True)
