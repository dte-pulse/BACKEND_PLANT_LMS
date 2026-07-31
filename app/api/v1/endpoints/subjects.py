from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_subject_service
from app.schemas.subject import SubjectCreate, SubjectRead, SubjectUpdate
from app.services.subject_service import SubjectService

router = APIRouter(prefix='/subjects', tags=['subjects'])


@router.get('', response_model=list[SubjectRead])
def list_subjects(service: SubjectService = Depends(get_subject_service)):
    return service.list_subjects()


@router.post('', response_model=SubjectRead)
def create_subject(payload: SubjectCreate, service: SubjectService = Depends(get_subject_service)):
    return service.create_subject(payload)


@router.get('/{subject_id}', response_model=SubjectRead)
def get_subject(subject_id: int, service: SubjectService = Depends(get_subject_service)):
    subject = service.get_subject(subject_id)
    if not subject:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Subject not found')
    return subject


@router.put('/{subject_id}', response_model=SubjectRead)
def update_subject(subject_id: int, payload: SubjectUpdate, service: SubjectService = Depends(get_subject_service)):
    subject = service.update_subject(subject_id, payload)
    if not subject:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Subject not found')
    return subject


@router.delete('/{subject_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(subject_id: int, service: SubjectService = Depends(get_subject_service)):
    success = service.delete_subject(subject_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Subject not found')
    return None
