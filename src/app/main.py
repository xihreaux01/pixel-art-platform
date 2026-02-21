from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.config import settings
from app.api.auth import router as auth_router
from app.api.credits import router as credits_router
from app.api.generations import router as generations_router
from app.api.provenance import router as provenance_router
from app.api.marketplace import router as marketplace_router
from app.api.webhooks import router as webhooks_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security import SecurityHeadersMiddleware

# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        (
            structlog.dev.ConsoleRenderer()
            if settings.APP_ENV == "development"
            else structlog.processors.JSONRenderer()
        ),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("starting_up", env=settings.APP_ENV)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.ping()
        log.info("redis_connected", url=settings.REDIS_URL)
    except Exception as e:
        log.warning("redis_connection_failed", error=str(e))
    app.state.redis = redis

    yield

    # Shutdown
    log.info("shutting_down")
    await redis.close()


app = FastAPI(
    title="Pixel Art Platform",
    lifespan=lifespan,
)

# CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.APP_ENV == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security headers (outermost — runs last on request, first on response)
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting (runs after security headers are already queued)
app.add_middleware(RateLimitMiddleware)


app.include_router(auth_router)
app.include_router(credits_router)
app.include_router(generations_router)
app.include_router(provenance_router)
app.include_router(marketplace_router)
app.include_router(webhooks_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/")
async def root_redirect():
    """Redirect root to the static SPA."""
    return RedirectResponse(url="/static/index.html")


# Mount static files (after all routers to avoid route conflicts)
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
