"""HTML pages and favicon."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.config import STATIC

router = APIRouter(tags=["pages"])


@router.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@router.get("/how-it-works")
def how_it_works():
    return FileResponse(STATIC / "how-it-works.html")


@router.get("/supported-leagues")
def supported_leagues():
    return FileResponse(STATIC / "supported-leagues.html")


@router.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "favicon.svg")
