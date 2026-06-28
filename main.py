from contextlib import asynccontextmanager
from fastapi import FastAPI
from telemetry.router import router as telemetry_router
from config.loader import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # startup
    app.state.settings = settings

    yield


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

    app.include_router(telemetry_router)
    return app

app = create_app()