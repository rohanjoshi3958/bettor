"""
ASGI entrypoint — run from `backend/`:

    uvicorn main:app --reload

Daily sports betting picks API. Set THE_ODDS_API_KEY for live odds (the-odds-api.com).
"""

from app.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="localhost", port=8000, reload=True)
