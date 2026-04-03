"""FastAPI application assembly."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.pages import router as pages_router
from app.api.picks import router as picks_router
from app.config import STATIC, load_environment

load_environment()

app = FastAPI(title="Bettor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(picks_router)
app.include_router(pages_router)

app.mount(
    "/assets",
    StaticFiles(directory=str(STATIC / "assets")),
    name="assets",
)
