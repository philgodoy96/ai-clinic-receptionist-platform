from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.middleware.request_correlation import RequestCorrelationMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
    )

    register_error_handlers(app)

    app.add_middleware(RequestCorrelationMiddleware)
    app.include_router(api_router)

    return app


app = create_app()
