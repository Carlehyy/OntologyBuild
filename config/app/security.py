from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware


class LocalRequestGuardMiddleware(BaseHTTPMiddleware):
    """Protect the local write API from DNS rebinding and browser CSRF."""

    def __init__(self, app, csrf_token: str, access_token: str):
        super().__init__(app)
        self.csrf_token = csrf_token
        self.access_token = access_token

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        host = (request.url.hostname or "").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "只允许从本机访问配置中心"}, status_code=400)

        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            submitted_access_token = request.headers.get(
                "x-config-access-token",
                "",
            )
            if not secrets.compare_digest(
                submitted_access_token,
                self.access_token,
            ):
                return JSONResponse(
                    {"detail": "本地访问令牌无效，请从启动窗口重新打开配置页面"},
                    status_code=403,
                )

        origin = (request.headers.get("origin") or "").strip()
        if origin:
            expected_origin = (
                f"{request.url.scheme}://{request.headers.get('host', '')}"
            )
            if origin != expected_origin:
                return JSONResponse({"detail": "请求来源不是当前配置中心"}, status_code=403)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            submitted = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(submitted, self.csrf_token):
                return JSONResponse({"detail": "页面令牌已失效，请刷新后重试"}, status_code=403)

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response
