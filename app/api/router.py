from fastapi import APIRouter

from app.api.routes import audit_logs, health, retell_tools, scheduling

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(scheduling.router)
api_router.include_router(retell_tools.router)
api_router.include_router(audit_logs.router)