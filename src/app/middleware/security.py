from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Headers applied to every response
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none'"
    ),
}

_HSTS_VALUE = "max-age=31536000; includeSubDomains"


def _apply_security_headers(response: Response, is_https: bool) -> None:
    """Set standard security headers on a response."""
    for name, value in _SECURITY_HEADERS.items():
        response.headers[name] = value
    if is_https:
        response.headers["Strict-Transport-Security"] = _HSTS_VALUE


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds standard security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        _apply_security_headers(response, is_https=request.url.scheme == "https")
        return response
