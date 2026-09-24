from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.mcq_service import McqService
from app.schemas.mcq import MCQRead, MCQPublicRead, MCQGenerateRequest, MCQEditRequest
from app.models.user import User, UserRole
from app.api.deps import get_current_user

router = APIRouter(prefix="/mcq", tags=["mcq"])

def get_mcq_service(db: Session = Depends(get_db)):
    return McqService(db)

# VULN-003: only authoring roles may see the answer key before submission.
def _can_view_answers(user: User) -> bool:
    return user.role in (UserRole.admin, UserRole.hod, UserRole.trainer)


def _serialize_mcqs(mcqs, user: User) -> list[dict]:
    """Role-aware serialization — trainees get MCQs without correct_option/explanation."""
    if _can_view_answers(user):
        return [MCQRead.model_validate(m).model_dump(mode='json') for m in mcqs]
    return [MCQPublicRead.model_validate(m).model_dump(mode='json') for m in mcqs]

@router.get("/document/{document_id}")
def list_mcqs(
    document_id: int,
    service: McqService = Depends(get_mcq_service),
    current_user: User = Depends(get_current_user),
):
    return _serialize_mcqs(service.list_mcqs_by_document(document_id), current_user)

@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_mcqs(
    payload: MCQGenerateRequest,
    service: McqService = Depends(get_mcq_service),
    current_user: User = Depends(get_current_user),
):
    try:
        created = service.generate_mcqs(payload.document_id, payload.count, payload.difficulty)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _serialize_mcqs(created, current_user)

from app.schemas.mcq import MCQAssessmentSubmit, MCQAssessmentResult
from app.models.user import User
from app.api.deps import get_current_user

@router.post("/submit", response_model=MCQAssessmentResult)
def submit_assessment(
    payload: MCQAssessmentSubmit,
    current_user: User = Depends(get_current_user),
    service: McqService = Depends(get_mcq_service)
):
    try:
        return service.submit_assessment(current_user.id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{mcq_id}", response_model=MCQRead)
def update_mcq(mcq_id: int, payload: MCQEditRequest, service: McqService = Depends(get_mcq_service)):
    try:
        return service.update_mcq(mcq_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/{mcq_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mcq(mcq_id: int, service: McqService = Depends(get_mcq_service)):
    success = service.delete_mcq(mcq_id)
    if not success:
        raise HTTPException(status_code=404, detail="MCQ not found")
    return None


@router.get("/document/{document_id}/final-assessment")
def get_final_assessment(
    document_id: int,
    service: McqService = Depends(get_mcq_service),
    current_user: User = Depends(get_current_user),
):
    return _serialize_mcqs(service.get_final_assessment(document_id), current_user)


@router.get("/document/{document_id}/effectiveness-exam")
def get_effectiveness_exam(
    document_id: int,
    service: McqService = Depends(get_mcq_service),
    current_user: User = Depends(get_current_user),
):
    return _serialize_mcqs(service.get_effectiveness_exam(document_id), current_user)


@router.post("/document/{document_id}/effectiveness-exam/submit", response_model=MCQAssessmentResult)
def submit_effectiveness_exam(
    document_id: int,
    payload: MCQAssessmentSubmit,
    current_user: User = Depends(get_current_user),
    service: McqService = Depends(get_mcq_service)
):
    from datetime import datetime
    payload.document_id = document_id
    try:
        res = service.submit_assessment(current_user.id, payload)
        if res["passed"]:
            from app.models.annexure import AnnexureV_TrainingRecord
            from app.models.document import Document
            from app.models.training import TrainingAssignment
            doc = service.db.query(Document).filter(Document.id == document_id).first()
            
            existing = service.db.query(AnnexureV_TrainingRecord).filter(
                AnnexureV_TrainingRecord.user_id == current_user.id,
                AnnexureV_TrainingRecord.document_ref == (doc.code if doc else None)
            ).first()
            if existing:
                existing.score = res["score"]
                existing.result = "PASS"
                existing.training_date = datetime.utcnow()
            else:
                record = AnnexureV_TrainingRecord(
                    user_id=current_user.id,
                    topic_title=doc.title if doc else "SOP Topic",
                    training_type="sop",
                    document_ref=doc.code if doc else None,
                    score=res["score"],
                    result="PASS",
                    training_date=datetime.utcnow()
                )
                service.db.add(record)

            # Check training type from assignment to update specific annexures
            assignment = service.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == current_user.id,
                TrainingAssignment.document_id == document_id
            ).first()
            training_type = assignment.training_type if assignment else "sop"

            if training_type == "cgmp":
                from app.models.annexure import AnnexureXI_CGMPRefresher
                existing_cgmp = service.db.query(AnnexureXI_CGMPRefresher).filter(
                    AnnexureXI_CGMPRefresher.user_id == current_user.id
                ).order_by(AnnexureXI_CGMPRefresher.id.desc()).first()
                if existing_cgmp:
                    existing_cgmp.score = res["score"]
                    existing_cgmp.result = "PASS"
                    existing_cgmp.training_date = datetime.utcnow()
                    service.db.add(existing_cgmp)
            elif training_type == "sop":
                from app.models.annexure import AnnexureX_SOPTraining
                existing_sop = service.db.query(AnnexureX_SOPTraining).filter(
                    AnnexureX_SOPTraining.user_id == current_user.id,
                    AnnexureX_SOPTraining.document_id == document_id
                ).order_by(AnnexureX_SOPTraining.id.desc()).first()
                if existing_sop:
                    existing_sop.score = res["score"]
                    existing_sop.result = "PASS"
                    existing_sop.training_date = datetime.utcnow()
                    service.db.add(existing_sop)

            service.db.commit()
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/final-assessment/{topic_id}/submit", response_model=MCQAssessmentResult)
def submit_topic_final_assessment(
    topic_id: int,
    payload: MCQAssessmentSubmit,
    current_user: User = Depends(get_current_user),
    service: McqService = Depends(get_mcq_service)
):
    """Submit topic-level final assessment answers and evaluate."""
    from app.models.document import Document
    doc = service.db.query(Document).filter(Document.topic_id == topic_id).first()
    if doc:
        payload.document_id = doc.id
    try:
        return service.submit_assessment(current_user.id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


