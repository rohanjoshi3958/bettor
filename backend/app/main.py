"""FastAPI application assembly."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.pages import router as pages_router
from app.api.picks import router as picks_router
from app.config import STATIC, get_cors_origins, load_environment
from app.logging_config import configure_logging, get_log_format, get_logger, log_event
from app.middleware import RequestLoggingMiddleware

load_environment()
configure_logging()

app = FastAPI(title="Bettor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # Origins are read from CORS_ALLOWED_ORIGINS (comma-separated) at startup.
    # See app/config.py — get_cors_origins() — for full documentation.
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)
# Outermost for requests: added last so it wraps CORS and sees the final status.
app.add_middleware(RequestLoggingMiddleware)

app.include_router(picks_router)
app.include_router(pages_router)

app.mount(
    "/assets",
    StaticFiles(directory=str(STATIC / "assets")),
    name="assets",
)

log_event(
    get_logger("bettor"),
    logging.INFO,
    "application_started",
    log_format=get_log_format(),
)
