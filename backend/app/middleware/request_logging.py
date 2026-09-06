"""HTTP request logging middleware with request-id correlation.

Implemented as pure ASGI (not ``BaseHTTPMiddleware``) to avoid known
Starlette/anyio stream leaks under ``TestClient``.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import log_event, request_id_var

logger = logging.getLogger("bettor.http")

_SKIP_PREFIXES = ("/assets/",)


class RequestLoggingMiddleware:
    """Assign ``X-Request-ID``, bind it to the log context, and log each response.

    Starlette's ``ServerErrorMiddleware`` sits outside user middleware and
    renders unhandled 500s via the original ``send`` callable, which would
    bypass a simple response-header wrapper. This middleware therefore catches
    unhandled exceptions before a response has started and emits its own 500
    that still includes ``X-Request-ID``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        incoming = headers.get("x-request-id", "").strip()
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False
        path = scope.get("path", "") or ""
        method = scope.get("method", "") or ""
        client = scope.get("client")
        client_host = client[0] if client else None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                raw_headers: list[tuple[bytes, bytes]] = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"x-request-id"
                ]
                raw_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Framework error middleware would send via the original ``send`` and
            # omit X-Request-ID. Handle the response here when nothing has started.
            if not response_started:
                status_code = 500
                await send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"x-request-id", request_id.encode("latin-1")),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Internal Server Error",
                    }
                )
                logger.exception(
                    "unhandled_request_error",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                    },
                )
            else:
                raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if not any(path.startswith(p) for p in _SKIP_PREFIXES):
                log_event(
                    logger,
                    logging.INFO,
                    "request_completed",
                    request_id=request_id,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=client_host,
                )
            request_id_var.reset(token)
