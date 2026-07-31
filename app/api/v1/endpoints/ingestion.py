from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_document_service, get_ingestion_service
from app.schemas.document import DocumentCreate
from app.schemas.ingestion import IngestionStatusResponse, UploadDocumentResponse
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.storage.file_storage import FileStorageService
from app.tasks.document_tasks import process_document

router = APIRouter(prefix='/ingestion', tags=['ingestion'])


@router.post('/upload', response_model=UploadDocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    code: str = Form(...),
    title: str = Form(...),
    topic: str = Form(...),
    topic_id: int | None = Form(None),
    subject_id: int | None = Form(None),
    version: int = Form(1),
    qa_scope: str = Form('doc_strict'),
    file: UploadFile = File(...),
    document_service: DocumentService = Depends(get_document_service),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {'.pdf', '.docx'}:
        raise HTTPException(status_code=400, detail='Only PDF and DOCX files are supported')

    from sqlalchemy.exc import IntegrityError
    
    storage = FileStorageService()
    stored_name, file_type, file_url = storage.save_upload(file)
    try:
        document = document_service.create_document(
        DocumentCreate(
            code=code,
            title=title,
            topic=topic,
            topic_id=topic_id,
            subject_id=subject_id,
            version=version,
            status='uploaded',
            qa_scope=qa_scope,
        )
        )
    except IntegrityError:
        document_service.repository.db.rollback()
        raise HTTPException(status_code=400, detail=f"Document with code '{code}' already exists.")
    document_service.repository.update(
        document,
        file_name=stored_name,
        file_type=file_type,
        file_url=file_url,
        status='uploaded',
    )
    process_document.delay(document.id)
    return {
        'document_id': document.id,
        'status': 'uploaded',
        'file_name': stored_name,
        'file_url': file_url,
        'task_name': 'app.tasks.process_document',
    }


@router.post('/process/{document_id}', response_model=IngestionStatusResponse)
def process_uploaded_document(document_id: int, ingestion_service: IngestionService = Depends(get_ingestion_service)):
    try:
        return ingestion_service.process_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/status/{document_id}', response_model=IngestionStatusResponse)
def get_ingestion_status(document_id: int, ingestion_service: IngestionService = Depends(get_ingestion_service)):
    try:
        return ingestion_service.get_status(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
