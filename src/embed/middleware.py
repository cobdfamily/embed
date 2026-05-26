"""Request-ID middleware.

Same shape as townsfolk's: every request gets an
X-Request-ID (caller-supplied or freshly minted),
echoed on the response, pinned to a contextvar so
log lines downstream pick it up without thread-
through.

Caller-supplied ids are accepted but capped at 128
chars to keep header sizes sane and prevent log-
line blowup from a malicious header.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


_current_id: ContextVar[str | None] = ContextVar(
    "embed_request_id", default=None,
)


def current_request_id() -> str | None:
    return _current_id.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Read/mint X-Request-ID. Place this OUTSIDE
    every other middleware so the id is set before
    anything else runs."""

    def __init__(self, app, *, header: str = "X-Request-ID"):
        super().__init__(app)
        self.header = header

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self.header, "")
        if incoming and len(incoming) <= 128:
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex
        token = _current_id.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _current_id.reset(token)
        response.headers[self.header] = request_id
        return response


class RequestIdLogFilter(logging.Filter):
    """Attach the current request id to every log
    record. Records outside a request carry rid='-'
    so log formatters don't have to handle None."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = current_request_id() or "-"
        return True
