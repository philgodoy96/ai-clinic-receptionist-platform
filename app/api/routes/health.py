from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.db.session import check_database_connection

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    settings = get_settings()

    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


@router.get("/health/dependencies")
def dependency_health_check() -> dict[str, Any]:
    settings = get_settings()
    database_status = _check_dependency(check_database_connection)

    status = "ok" if database_status["status"] == "ok" else "degraded"

    return {
        "status": status,
        "service": settings.app_name,
        "environment": settings.app_env,
        "dependencies": {
            "database": database_status,
        },
    }


def _check_dependency(check: Callable[[], None]) -> dict[str, str]:
    try:
        check()
    except Exception as exc:
        return {
            "status": "unavailable",
            "detail": exc.__class__.__name__,
        }

    return {"status": "ok"}
