from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_document_service
from app.db.session import get_db
from app.repositories.chunk_repository import ChunkRepository
from app.schemas.chunk import ChunkRead
from app.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from app.services.document_service import DocumentService

router = APIRouter(prefix='/documents', tags=['documents'])


@router.get('', response_model=list[DocumentRead])
def list_documents(service: DocumentService = Depends(get_document_service)):
    return service.list_documents()


@router.post('', response_model=DocumentRead)
def create_document(payload: DocumentCreate, service: DocumentService = Depends(get_document_service)):
    return service.create_document(payload)


@router.get('/{document_id}', response_model=DocumentRead)
def get_document(document_id: int, service: DocumentService = Depends(get_document_service)):
    doc = service.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    return doc


@router.get('/{document_id}/history-summary')
def get_document_history_summary(document_id: int, service: DocumentService = Depends(get_document_service)):
    summary = service.get_document_history_summary(document_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    return summary


@router.put('/{document_id}', response_model=DocumentRead)
def update_document(document_id: int, payload: DocumentUpdate, service: DocumentService = Depends(get_document_service)):
    doc = service.update_document(document_id, payload.model_dump(exclude_unset=True))
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    return doc


@router.delete('/{document_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, service: DocumentService = Depends(get_document_service)):
    success = service.delete_document(document_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    return None


@router.post('/{document_id}/publish', response_model=DocumentRead)
def publish_document(document_id: int, service: DocumentService = Depends(get_document_service)):
    doc = service.update_document(document_id, {'status': 'active'})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    return doc


@router.post('/{document_id}/archive', response_model=DocumentRead)
def archive_document(document_id: int, service: DocumentService = Depends(get_document_service)):
    doc = service.update_document(document_id, {'status': 'archived'})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    return doc


@router.get('/{document_id}/chunks', response_model=list[ChunkRead])
def get_document_chunks(document_id: int, db: Session = Depends(get_db)):
    repo = ChunkRepository(db)
    return repo.get_by_document(document_id)


@router.get('/{document_id}/view-url')
def get_document_view_url(document_id: int, service: DocumentService = Depends(get_document_service)):
    doc = service.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    
    if not doc.file_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document has no file associated')
        
    from app.storage.file_storage import FileStorageService
    storage = FileStorageService()
    url = storage.get_presigned_url(doc.file_name)
    
    if not url:
        # Fallback to local URL if generated incorrectly
        url = doc.file_url

    return {'url': url}
