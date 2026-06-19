from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AI Clinic Receptionist Platform",
        debug=settings.app_debug,
        version="0.1.0",
    )

    app.include_router(api_router)

    return app


app = create_app()