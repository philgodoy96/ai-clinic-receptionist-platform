from fastapi import APIRouter

from app.api.routes import (
    audit_logs,
    chat,
    email_jobs,
    health,
    human_escalations,
    retell_lifecycle,
    retell_tools,
    scheduling,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(scheduling.router)
api_router.include_router(chat.router)
api_router.include_router(retell_tools.router)
api_router.include_router(retell_lifecycle.router)
api_router.include_router(audit_logs.router)
api_router.include_router(email_jobs.router)
api_router.include_router(human_escalations.router)