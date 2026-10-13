from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from jobqueue.circuit_breaker import RedisCircuitBreaker
from jobqueue.rate_limiter import RedisRateLimiter
from sqlalchemy.exc import IntegrityError

from api.v1.admin import router as admin_router
from api.v1.auth import router as auth_router
from api.v1.dead_letters import router as dead_letters_router
from api.v1.devices import router as devices_router
from api.v1.tenant import router as tenant_router
from api.v1.events import router as events_router
from api.v1.metrics import router as metrics_router
from api.v1.notifications import router as notifications_router
from api.v1.preferences import router as preferences_router
from api.v1.providers import catalog_router as provider_catalog_router
from api.v1.providers import router as providers_router
from api.v1.sse import router as sse_router
from api.v1.templates import router as templates_router
from api.v1.webhooks import router as webhooks_router
from api.v1.workflows import router as workflows_router
from clients.sentinel_client import DatabaseRecipientClient
from config.loader import get_settings
from database.database import Database
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import IdentityService
from services.metrics_service import MetricsService
from services.rate_limit_service import RateLimitService
from services.notification_service import NotificationService
from services.rendering_service import RenderingService
from sse.registry import SseConnectionRegistry
from telemetry.router import router as telemetry_router
from workflows.registry import WorkflowRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # startup
    app.state.settings = settings
    app.state.database = Database(settings.database)
    await app.state.database.initialize()

    app.state.redis = Redis(
        host=settings.redis.host,
        port=settings.redis.port,
        db=settings.redis.db,
        password=settings.redis.password,
    )
    app.state.sse_registry = SseConnectionRegistry(app.state.redis)
    app.state.circuit_breaker = RedisCircuitBreaker(app.state.redis, settings.breaker)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(app.state.database.session_factory)

    app.state.notification_service = NotificationService(
        uow_factory=uow_factory,
        sentinel=DatabaseRecipientClient(uow_factory),
        registry=WorkflowRegistry(),
        rendering=RenderingService(),
    )
    app.state.rate_limit_service = RateLimitService(
        RedisRateLimiter(app.state.redis), uow_factory, settings.rate_limit
    )
    app.state.metrics_service = MetricsService(uow_factory)
    app.state.identity_service = IdentityService(uow_factory)

    yield

    # shutdown
    await app.state.redis.aclose()
    await app.state.database.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        docs_url=settings.api.docs_url,
        redoc_url=settings.api.redoc_url,
        openapi_url=settings.api.openapi_url,
        lifespan=lifespan,
    )

    @app.exception_handler(IntegrityError)
    async def integrity_conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        # Unique-constraint violations surface as 409, not 500.
        return JSONResponse(
            status_code=409,
            content={"detail": "Resource conflicts with an existing record"},
        )

    app.include_router(telemetry_router)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(tenant_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(devices_router, prefix="/api/v1")
    app.include_router(dead_letters_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(sse_router, prefix="/api/v1")
    app.include_router(templates_router, prefix="/api/v1")
    app.include_router(workflows_router, prefix="/api/v1")
    app.include_router(preferences_router, prefix="/api/v1")
    app.include_router(provider_catalog_router, prefix="/api/v1")
    app.include_router(providers_router, prefix="/api/v1")
    app.include_router(metrics_router, prefix="/api/v1")

    ui_dir = Path(__file__).parent / "ui"
    if ui_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def index() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

    return app

app = create_app()
