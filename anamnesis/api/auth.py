"""API key authentication middleware for Anamnesis."""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("anamnesis.auth")


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Simple API key authentication.

    If ANAMNESIS_API_KEY is set, all requests must include it
    in the Authorization header as 'Bearer <key>'.
    If not set, authentication is disabled.
    """

    def __init__(self, app, api_key: str = ""):
        super().__init__(app)
        self.api_key = api_key
        if api_key:
            logger.info("API key authentication enabled")
        else:
            logger.info("API key authentication disabled (no ANAMNESIS_API_KEY)")

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health endpoint and docs
        if request.url.path in ("/api/v1/health", "/docs", "/openapi.json"):
            return await call_next(request)

        if self.api_key:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing Authorization header"},
                )
            token = auth[7:]
            if token != self.api_key:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid API key"},
                )

        return await call_next(request)
