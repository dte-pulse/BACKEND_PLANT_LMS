from fastapi import APIRouter, Depends
from app.api.deps import get_current_user

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.ingestion import router as ingestion_router
from app.api.v1.endpoints.subjects import router as subjects_router
from app.api.v1.endpoints.topics import router as topics_router
from app.api.v1.endpoints.training import router as training_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.mcq import router as mcq_router
from app.api.v1.endpoints.learning import router as learning_router
from app.api.v1.endpoints.qa import router as qa_router
from app.api.v1.endpoints.notifications import router as notifications_router
from app.api.v1.endpoints.reports import router as reports_router
from app.api.v1.endpoints.calendar import router as calendar_router
from app.api.v1.endpoints.attendance import router as attendance_router
from app.api.v1.endpoints.annexure import router as annexure_router
from app.api.v1.endpoints.departments import router as departments_router
from app.api.v1.endpoints.learning_session import router as learning_session_router
from app.api.v1.endpoints.document_assignments import router as document_assignments_router
from app.api.v1.endpoints.observability import router as observability_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)

# All routers below require the user to be authenticated
auth_deps = [Depends(get_current_user)]

api_router.include_router(users_router, dependencies=auth_deps)
api_router.include_router(departments_router, dependencies=auth_deps)
api_router.include_router(subjects_router, dependencies=auth_deps)
api_router.include_router(topics_router, dependencies=auth_deps)
api_router.include_router(documents_router, dependencies=auth_deps)
api_router.include_router(document_assignments_router, dependencies=auth_deps)
api_router.include_router(ingestion_router, dependencies=auth_deps)
api_router.include_router(training_router, dependencies=auth_deps)
api_router.include_router(mcq_router, dependencies=auth_deps)
api_router.include_router(learning_router, dependencies=auth_deps)
api_router.include_router(learning_session_router, dependencies=auth_deps)
api_router.include_router(qa_router, dependencies=auth_deps)
api_router.include_router(notifications_router, dependencies=auth_deps)
api_router.include_router(reports_router, dependencies=auth_deps)
api_router.include_router(observability_router, dependencies=auth_deps)
api_router.include_router(calendar_router, dependencies=auth_deps)
api_router.include_router(attendance_router, dependencies=auth_deps)
api_router.include_router(annexure_router, dependencies=auth_deps)

