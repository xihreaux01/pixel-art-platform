import time
from dataclasses import dataclass

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

log = structlog.get_logger()


# Rate limit rules as a list so we can have multiple rules for the same path.
# Rules are matched top-to-bottom; the first matching rule wins.
RATE_LIMIT_RULES = [
    {
        "path": "/api/v1/auth/register",
        "limit": 3,
        "window": 3600,
        "key": "ip",
    },
    {
        "path": "/api/v1/auth/login",
        "limit": 5,
        "window": 900,
        "key": "user",
    },
    {
        "path": "/api/v1/generations",
        "limit": 10,
        "window": 3600,
        "key": "user",
        "method": "POST",
    },
    {
        "path": "/api/v1/credits/purchase",
        "limit": 5,
        "window": 3600,
        "key": "user",
    },
    {
        "path": "/api/v1/marketplace",
        "limit": 100,
        "window": 60,
        "key": "user",
    },
]

SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@dataclass
class RateLimitResult:
    """Holds the outcome of a sliding-window rate limit check."""

    current_count: int
    limit: int
    window: int
    reset_at: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.current_count)

    @property
    def exceeded(self) -> bool:
        return self.current_count > self.limit


def _find_matching_rule(path: str, method: str) -> dict | None:
    """Return the first rate limit rule that matches the request path and method."""
    for rule in RATE_LIMIT_RULES:
        if not path.startswith(rule["path"]):
            continue
        required_method = rule.get("method")
        if required_method and required_method.upper() != method.upper():
            continue
        return rule
    return None


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For when present."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolve_identifier(request: Request, rule: dict) -> str:
    """Build the identifier string for rate limiting based on the rule key type."""
    if rule["key"] == "ip":
        return _get_client_ip(request)
    user_id = getattr(request.state, "user_id", None)
    return user_id or _get_client_ip(request)


async def _check_rate_limit(redis, redis_key: str, rule: dict, request: Request) -> RateLimitResult:
    """Execute the sliding window check against Redis and return the result."""
    now = int(time.time())
    window = rule["window"]

    pipe = redis.pipeline()
    pipe.zremrangebyscore(redis_key, 0, now - window)
    pipe.zadd(redis_key, {f"{now}:{id(request)}": now})
    pipe.zcard(redis_key)
    pipe.expire(redis_key, window)
    results = await pipe.execute()

    return RateLimitResult(
        current_count=results[2],
        limit=rule["limit"],
        window=window,
        reset_at=now + window,
    )


def _add_rate_limit_headers(response: Response, result: RateLimitResult) -> None:
    """Attach X-RateLimit-* headers to a response."""
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_at)


def _build_429_response(result: RateLimitResult) -> JSONResponse:
    """Create a 429 Too Many Requests response with rate limit headers."""
    response = JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )
    _add_rate_limit_headers(response, result)
    response.headers["Retry-After"] = str(result.window)
    return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed sliding window rate limiter."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        rule = _find_matching_rule(request.url.path, request.method)
        if rule is None:
            return await call_next(request)

        identifier = _resolve_identifier(request, rule)
        redis_key = f"ratelimit:{rule['path']}:{identifier}"

        try:
            redis = request.app.state.redis
            result = await _check_rate_limit(redis, redis_key, rule, request)

            if result.exceeded:
                log.warning(
                    "rate_limit_exceeded",
                    path=request.url.path,
                    identifier=identifier,
                    limit=result.limit,
                    count=result.current_count,
                )
                return _build_429_response(result)

            response = await call_next(request)
            _add_rate_limit_headers(response, result)
            return response

        except Exception as exc:
            log.warning("rate_limit_redis_error", error=str(exc), path=request.url.path)
            return await call_next(request)
