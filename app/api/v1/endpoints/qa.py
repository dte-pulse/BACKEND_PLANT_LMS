from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.qa import QARequest, QAResponse, QAResolveRequest
from app.services.qa_service import QaService

router = APIRouter(prefix='/qa', tags=['qa'])

def get_qa_service(db: Session = Depends(get_db)):
    return QaService(db)

@router.get('', response_model=list[QAResponse])
def get_all_qa(
    document_id: int | None = None,
    current_user: User = Depends(get_current_user),
    service: QaService = Depends(get_qa_service)
):
    return service.list_user_qa(current_user.id, document_id=document_id)

@router.post('', response_model=QAResponse)
def ask_question(
    payload: QARequest,
    current_user: User = Depends(get_current_user),
    service: QaService = Depends(get_qa_service)
):
    return service.process_question(current_user.id, payload)

@router.put('/{qa_id}', response_model=QAResponse)
def resolve_question(
    qa_id: int,
    payload: QAResolveRequest,
    current_user: User = Depends(get_current_user),
    service: QaService = Depends(get_qa_service)
):
    qa = service.resolve_session(current_user.id, qa_id, payload)
    if not qa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='QA session not found or not owned by user')
    return qa
