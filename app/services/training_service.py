from datetime import datetime, timezone

from app.models.annexure import (
    AnnexureI_InductionSchedule,
    AnnexureVII_NeedBasedTraining,
    AnnexureVIII_ExternalTraining,
    AnnexureIX_OJTRecord,
    AnnexureX_SOPTraining,
    AnnexureXI_CGMPRefresher,
)
from app.models.document import Document
from app.models.training import TrainingAssignment
from app.models.training_evidence import TrainingEvidence
from app.models.user import User
from app.repositories.training_repository import TrainingRepository
from app.schemas.training import TrainingAssignmentCreate
from app.storage.file_storage import FileStorageService


class TrainingService:
    def __init__(self, repository: TrainingRepository):
        self.repository = repository
        self.db = repository.db

    def _invalidate_caches(self, *user_ids: int | None):
        """Best-effort: drop cached report/dashboard/learning data affected by a
        training-assignment write so the next read is fresh."""
        from app.services.response_cache import invalidate_cached
        prefixes = ['resp:report:', 'resp:paths:']
        for uid in user_ids:
            if uid:
                prefixes.append(f'resp:learning:{uid}:')
        invalidate_cached(*prefixes)

    def _get_user(self, user_id: int) -> User | None:
        return self.db.query(User).filter(User.id == user_id).first()

    def _get_document(self, document_id: int | None) -> Document | None:
        if not document_id:
            return None
        return self.db.query(Document).filter(Document.id == document_id).first()

    def _create_annexure_record(self, model_class, **kwargs):
        record = model_class(**kwargs)
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def _sync_induction_annexure(self, assignment: TrainingAssignment):
        user = self._get_user(assignment.user_id)
        document = self._get_document(assignment.document_id)
        if not user:
            return
        self._create_annexure_record(
            AnnexureI_InductionSchedule,
            user_id=assignment.user_id,
            department=user.department or 'Unassigned',
            scheduled_start=assignment.created_at,
            scheduled_end=assignment.due_date,
            trainer_id=assignment.trainer_id,
            topics_covered=document.title if document else assignment.notes,
            status=assignment.status or 'planned',
            notes=assignment.notes,
        )

    def _sync_need_based_annexure(self, assignment: TrainingAssignment):
        user = self._get_user(assignment.user_id)
        document = self._get_document(assignment.document_id)
        if not user:
            return
        self._create_annexure_record(
            AnnexureVII_NeedBasedTraining,
            requester_id=assignment.user_id,
            department=user.department or 'Unassigned',
            training_need=document.title if document else 'Need-based training request',
            reason=assignment.requested_reason or assignment.notes or 'Requested from LMS workflow',
            target_employees=str([assignment.user_id]),
            requested_date=assignment.created_at,
            status=assignment.status,
            approval_remarks=assignment.notes,
        )

    def _sync_external_annexure(self, assignment: TrainingAssignment):
        document = self._get_document(assignment.document_id)
        self._create_annexure_record(
            AnnexureVIII_ExternalTraining,
            user_id=assignment.user_id,
            training_title=document.title if document else assignment.notes or 'External training',
            agency_name=assignment.external_provider or 'External provider',
            venue=assignment.external_venue,
            start_date=assignment.created_at,
            end_date=assignment.completed_at or assignment.created_at,
            duration_days=(assignment.external_duration_hours / 8) if assignment.external_duration_hours else 1.0,
            certificate_url=assignment.certificate_url,
            learning_summary=assignment.notes,
        )

    def _sync_ojt_annexure(self, assignment: TrainingAssignment):
        document = self._get_document(assignment.document_id)
        topic_title = document.topic if document else assignment.notes or 'OJT Training'
        existing = (
            self.db.query(AnnexureIX_OJTRecord)
            .filter(
                AnnexureIX_OJTRecord.user_id == assignment.user_id,
                AnnexureIX_OJTRecord.trainer_id == assignment.trainer_id,
                AnnexureIX_OJTRecord.topic_title == topic_title,
            )
            .order_by(AnnexureIX_OJTRecord.id.desc())
            .first()
        )
        if existing:
            existing.end_date = assignment.completed_at or datetime.now(timezone.utc)
            existing.result = 'qualified' if assignment.verified_by_trainer else existing.result
            self.db.commit()
            self.db.refresh(existing)
            return existing
        return self._create_annexure_record(
            AnnexureIX_OJTRecord,
            user_id=assignment.user_id,
            trainer_id=assignment.trainer_id,
            topic_id=document.topic_id if document else None,
            topic_title=topic_title,
            start_date=assignment.created_at,
            end_date=assignment.completed_at or datetime.now(timezone.utc),
            tasks_performed=assignment.notes,
            trainer_observation='Verified via LMS OJT workflow',
            result='qualified' if assignment.verified_by_trainer else 'pending',
            work_allotted=assignment.verified_by_trainer,
        )

    def _sync_sop_annexure(self, assignment: TrainingAssignment):
        document = self._get_document(assignment.document_id)
        if not document:
            return
            
        trigger_reason = 'new_sop'
        if document.version > 1:
            trigger_reason = 'revision'
        if assignment.notes and 'periodic' in assignment.notes.lower():
            trigger_reason = 'periodic'

        self._create_annexure_record(
            AnnexureX_SOPTraining,
            user_id=assignment.user_id,
            document_id=document.id,
            sop_code=document.code,
            sop_title=document.title,
            sop_version=document.version,
            training_date=assignment.created_at,
            trigger_reason=trigger_reason,
            result='pending',
            trainer_id=assignment.trainer_id,
        )

    def _sync_cgmp_annexure(self, assignment: TrainingAssignment):
        self._create_annexure_record(
            AnnexureXI_CGMPRefresher,
            user_id=assignment.user_id,
            year=datetime.now(timezone.utc).year,
            training_date=assignment.created_at,
            topics_covered=assignment.notes or 'cGMP refresher',
            result='pending',
            trainer_id=assignment.trainer_id,
        )

    def create_assignment(self, payload: TrainingAssignmentCreate):
        assignment = TrainingAssignment(
            user_id=payload.user_id,
            document_id=payload.document_id,
            training_type=payload.training_type,
            status=payload.status,
            trainer_id=payload.trainer_id,
            due_date=payload.due_date,
            certificate_url=payload.certificate_url,
            assigned_by_id=payload.assigned_by_id,
            notes=payload.notes,
            requested_reason=payload.requested_reason,
            external_provider=payload.external_provider,
            external_venue=payload.external_venue,
            external_duration_hours=payload.external_duration_hours,
            verified_by_trainer=False
        )
        assignment = self.repository.create(assignment)
        if assignment.training_type == "induction":
            self._sync_induction_annexure(assignment)
        elif assignment.training_type == "sop":
            self._sync_sop_annexure(assignment)
        elif assignment.training_type == "cgmp":
            self._sync_cgmp_annexure(assignment)
        self._invalidate_caches(assignment.user_id)
        return assignment

    def list_assignments(self, *, department: str | None = None, user_id: int | None = None, training_type: str | None = None, status: str | None = None):
        return self.repository.list_all(
            department=department,
            user_id=user_id,
            training_type=training_type,
            status=status,
        )

    def list_user_assignments(self, user_id: int):
        return self.repository.get_by_user_id(user_id)

    def complete_assignment(self, assignment_id: int, user_id: int):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment.user_id != user_id:
            raise PermissionError("Not authorized to modify this assignment")
        
        # If it's an OJT assignment, it needs verification
        if assignment.training_type == 'ojt' and not assignment.verified_by_trainer:
            result = self.repository.update(assignment, status="pending_verification")
            self._invalidate_caches(assignment.user_id)
            return result
        
        result = self.repository.update(
            assignment,
            status="completed",
            completed_at=datetime.now(timezone.utc)
        )
        self._award_completion_coins(assignment)
        self._invalidate_caches(assignment.user_id)
        return result

    def verify_ojt(self, assignment_id: int, trainer_id: int):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment.trainer_id != trainer_id:
            raise PermissionError("Not authorized as trainer for this assignment")
        
        assignment = self.repository.update(
            assignment,
            verified_by_trainer=True,
            status="completed",
            completed_at=datetime.now(timezone.utc)
        )
        self._sync_ojt_annexure(assignment)
        self._award_completion_coins(assignment)
        self._invalidate_caches(assignment.user_id)
        return assignment

    def _award_completion_coins(self, assignment) -> None:
        """Reward assignment completion with coins (gamification).

        Never breaks the completion flow: gamification failures are swallowed.
        """
        try:
            from app.services.gamification_service import GamificationService
            GamificationService(self.db).notify_event(
                assignment.user_id,
                'assignment_completed',
                assignment_id=str(assignment.id),
                document_id=assignment.document_id,
            )
        except Exception:  # noqa: BLE001 — rewards must never break learning
            pass

    def trigger_induction_training(self, user_id: int, document_ids: list[int], assigned_by_id: int | None = None):
        assignments = []
        for doc_id in document_ids:
            a = self.create_assignment(TrainingAssignmentCreate(
                user_id=user_id,
                document_id=doc_id,
                training_type="induction",
                status="pending",
                assigned_by_id=assigned_by_id,
            ))
            assignments.append(a)
        return assignments
        
    def log_external_training(
        self,
        user_id: int,
        document_id: int,
        certificate_url: str | None = None,
        *,
        notes: str | None = None,
        provider: str | None = None,
        venue: str | None = None,
        duration_hours: int | None = None,
        assigned_by_id: int | None = None,
    ):
        assignment = self.create_assignment(TrainingAssignmentCreate(
            user_id=user_id, document_id=document_id, training_type="external", 
            status="completed",
            certificate_url=certificate_url,
            notes=notes,
            external_provider=provider,
            external_venue=venue,
            external_duration_hours=duration_hours,
            assigned_by_id=assigned_by_id,
        ))
        assignment = self.repository.update(
            assignment,
            completed_at=datetime.now(timezone.utc),
        )
        self._sync_external_annexure(assignment)
        return assignment

    def submit_need_based_request(
        self,
        user_id: int,
        document_id: int,
        reason: str,
        *,
        notes: str | None = None,
        assigned_by_id: int | None = None,
    ):
        # A request starts as 'pending_approval' from HOD
        assignment = self.create_assignment(TrainingAssignmentCreate(
            user_id=user_id,
            document_id=document_id,
            training_type="need_based",
            status="pending_approval",
            requested_reason=reason,
            notes=notes,
            assigned_by_id=assigned_by_id,
        ))
        self._sync_need_based_annexure(assignment)
        return assignment

    def trigger_cgmp_refresher(self, user_ids: list[int], document_id: int, assigned_by_id: int | None = None, notes: str | None = None):
        assignments = []
        for uid in user_ids:
            a = self.create_assignment(TrainingAssignmentCreate(
                user_id=uid,
                document_id=document_id,
                training_type="cgmp",
                status="pending",
                assigned_by_id=assigned_by_id,
                notes=notes,
            ))
            assignments.append(a)
        return assignments

    def trigger_sop_training(self, user_ids: list[int], document_id: int, assigned_by_id: int | None = None, notes: str | None = None):
        assignments = []
        for uid in user_ids:
            a = self.create_assignment(TrainingAssignmentCreate(
                user_id=uid,
                document_id=document_id,
                training_type="sop",
                status="pending",
                assigned_by_id=assigned_by_id,
                notes=notes,
            ))
            assignments.append(a)
        return assignments

    def assign_contractual_training(self, user_id: int, document_ids: list[int], assigned_by_id: int | None = None, notes: str | None = None):
        assignments = []
        for doc_id in document_ids:
            a = self.create_assignment(TrainingAssignmentCreate(
                user_id=user_id,
                document_id=doc_id,
                training_type="contractual",
                status="pending",
                assigned_by_id=assigned_by_id,
                notes=notes,
            ))
            assignments.append(a)
        return assignments

    def get_assignment(self, assignment_id: int):
        return self.repository.get_by_id(assignment_id)

    def update_assignment(self, assignment_id: int, **kwargs):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")
        result = self.repository.update(assignment, **kwargs)
        self._invalidate_caches(assignment.user_id)
        return result

    def delete_assignment(self, assignment_id: int):
        assignment = self.repository.get_by_id(assignment_id)
        ok = self.repository.delete(assignment_id)
        if ok and assignment:
            self._invalidate_caches(assignment.user_id)
        return ok

    def review_assignment(self, assignment_id: int, reviewer_id: int, approved: bool, notes: str | None = None):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment.status != "pending_approval":
            raise ValueError("Only pending approval assignments can be reviewed")

        next_status = "assigned" if approved else "rejected"
        result = self.repository.update(
            assignment,
            status=next_status,
            approved_by_id=reviewer_id,
            approval_notes=notes,
            approved_at=datetime.now(timezone.utc),
        )
        self._invalidate_caches(assignment.user_id)
        return result

    def list_assignment_evidence(self, assignment_id: int):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")
        return (
            self.db.query(TrainingEvidence)
            .filter(TrainingEvidence.assignment_id == assignment_id)
            .order_by(TrainingEvidence.id.desc())
            .all()
        )

    def get_evidence(self, evidence_id: int):
        return self.db.query(TrainingEvidence).filter(TrainingEvidence.id == evidence_id).first()

    def add_assignment_evidence(self, assignment_id: int, uploaded_by_id: int, upload, label: str | None = None):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")

        storage = FileStorageService()
        stored_name, file_type, file_url = storage.save_upload(upload)
        evidence = TrainingEvidence(
            assignment_id=assignment_id,
            uploaded_by_id=uploaded_by_id,
            label=label,
            file_name=upload.filename or stored_name,
            stored_name=stored_name,
            file_type=file_type,
            file_url=file_url,
        )
        self.db.add(evidence)
        self.db.commit()
        self.db.refresh(evidence)

        # Sync evidence URL to external training records
        if assignment.training_type == "external":
            download_path = f"/training/evidence/{evidence.id}/download"
            assignment.certificate_url = download_path
            self.db.add(assignment)

            from app.models.annexure import AnnexureVIII_ExternalTraining
            annex_record = (
                self.db.query(AnnexureVIII_ExternalTraining)
                .filter(
                    AnnexureVIII_ExternalTraining.user_id == assignment.user_id,
                    AnnexureVIII_ExternalTraining.training_title == (
                        self._get_document(assignment.document_id).title if assignment.document_id else (assignment.notes or 'External training')
                    )
                )
                .order_by(AnnexureVIII_ExternalTraining.id.desc())
                .first()
            )
            if annex_record:
                annex_record.certificate_url = download_path
                self.db.add(annex_record)

            self.db.commit()

        return evidence
