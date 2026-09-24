from app.models.document import Document
from app.repositories.document_repository import DocumentRepository
from app.schemas.document import DocumentCreate


class DocumentService:
    def __init__(self, repository: DocumentRepository):
        self.repository = repository

    def create_document(self, payload: DocumentCreate):
        # Resolve topic_id and subject_id if not explicitly provided
        db = self.repository.db
        topic_id = payload.topic_id
        subject_id = payload.subject_id
        
        if not topic_id and payload.topic:
            # Query for topic by title (case-insensitive)
            from app.models.topic import Topic
            from app.models.subject import Subject
            existing_topic = db.query(Topic).filter(Topic.title.ilike(payload.topic.strip())).first()
            if existing_topic:
                topic_id = existing_topic.id
                subject_id = existing_topic.subject_id
            else:
                # If no matching topic, find or create a default "General" subject and create topic under it
                general_subject = db.query(Subject).filter(Subject.name.ilike('General')).first()
                if not general_subject:
                    general_subject = Subject(name='General', department='General')
                    db.add(general_subject)
                    db.commit()
                    db.refresh(general_subject)
                
                # Get max sequence order
                from sqlalchemy import func
                max_order = db.query(func.max(Topic.sequence_order)).filter(Topic.subject_id == general_subject.id).scalar() or 0
                
                new_topic = Topic(
                    subject_id=general_subject.id,
                    title=payload.topic.strip(),
                    sequence_order=(max_order or 0) + 1
                )
                db.add(new_topic)
                db.commit()
                db.refresh(new_topic)
                
                topic_id = new_topic.id
                subject_id = general_subject.id

        # Check versioning: if document with same code exists, increment version
        existing_doc = db.query(Document).filter(
            Document.code == payload.code
        ).order_by(Document.version.desc()).first()
        
        version = payload.version
        if existing_doc:
            version = existing_doc.version + 1

        document = Document(
            code=payload.code,
            title=payload.title,
            topic=payload.topic,
            topic_id=topic_id,
            subject_id=subject_id,
            version=version,
            sequence_order=payload.sequence_order,
            status=payload.status,
            qa_scope=payload.qa_scope,
            is_latest=False,  # default to False until published (atomic flip)
        )
        return self.repository.create(document)

    def list_documents(self):
        return self.repository.list_all()

    def get_document(self, document_id: int):
        return self.repository.get_by_id(document_id)

    def _collect_publish_impact_details(self, document_id: int):
        db = self.repository.db
        from app.models.annexure import DocumentAssignment
        from app.models.training import TrainingAssignment
        from app.models.user import User

        user_ids: set[int] = set()
        impacted_departments: set[str] = set()
        dept_assignment_departments: set[str] = set()
        direct_assignments = db.query(DocumentAssignment).filter(DocumentAssignment.document_id == document_id).all()
        for da in direct_assignments:
            if da.user_id:
                user_ids.add(da.user_id)
            elif da.department:
                impacted_departments.add(da.department)
                dept_assignment_departments.add(da.department)

        existing_assignments = db.query(TrainingAssignment).filter(TrainingAssignment.document_id == document_id).all()
        for assignment in existing_assignments:
            user_ids.add(assignment.user_id)

        # N+1 fix: batch-fetch all referenced users + department rosters in two queries.
        impacted_users: dict[int, dict] = {}
        if user_ids:
            for user in db.query(User).filter(User.id.in_(user_ids)).all():
                impacted_users[user.id] = {
                    'user_id': user.id,
                    'full_name': user.full_name,
                    'employee_code': user.employee_code,
                    'department': user.department,
                }
        if dept_assignment_departments:
            for user in db.query(User).filter(
                User.department.in_(dept_assignment_departments),
                User.is_active == True,
            ).all():
                user_ids.add(user.id)
                impacted_users[user.id] = {
                    'user_id': user.id,
                    'full_name': user.full_name,
                    'employee_code': user.employee_code,
                    'department': user.department,
                }

        return {
            'user_ids': user_ids,
            'impacted_users': sorted(impacted_users.values(), key=lambda row: row['full_name']),
            'impacted_departments': sorted(impacted_departments),
        }

    def get_document_history_summary(self, document_id: int):
        db = self.repository.db
        document = self.repository.get_by_id(document_id)
        if not document:
            return None

        related_versions = (
            db.query(Document)
            .filter(Document.code == document.code)
            .order_by(Document.version.desc(), Document.id.desc())
            .all()
        )

        from app.models.training import TrainingAssignment
        from app.models.user_progress import UserProgress
        from app.models.annexure import DocumentAssignment

        impact_details = self._collect_publish_impact_details(document_id)
        impacted_user_ids = impact_details['user_ids']
        progress_records = db.query(UserProgress).filter(UserProgress.document_id == document_id).all()
        training_assignments = db.query(TrainingAssignment).filter(TrainingAssignment.document_id == document_id).all()
        document_assignments = db.query(DocumentAssignment).filter(DocumentAssignment.document_id == document_id).all()

        return {
            'document_id': document.id,
            'code': document.code,
            'current_version': document.version,
            'current_status': document.status,
            'version_history': [
                {
                    'id': doc.id,
                    'version': doc.version,
                    'title': doc.title,
                    'status': doc.status,
                    'created_at': doc.created_at.isoformat() if getattr(doc, 'created_at', None) else None,
                }
                for doc in related_versions
            ],
            'publish_impact': {
                'impacted_user_count': len(impacted_user_ids),
                'training_assignment_count': len(training_assignments),
                'document_assignment_count': len(document_assignments),
                'progress_records_to_reset': len(progress_records),
                'completed_progress_records': sum(1 for row in progress_records if (row.completion_percentage or 0) >= 100.0),
                'impacted_departments': impact_details['impacted_departments'],
                'impacted_users': impact_details['impacted_users'],
            },
        }

    def update_document(self, document_id: int, payload: dict):
        document = self.repository.get_by_id(document_id)
        if not document:
            return None
            
        # Invalidate Semantic Cache
        from app.services.semantic_cache_service import SemanticCacheService
        try:
            cache = SemanticCacheService()
            cache.invalidate_document(document_id)
        except Exception:
            pass

        updated_doc = self.repository.update(document, **payload)

        # Trigger retraining on publish
        if payload.get('status') == 'active':
            db = self.repository.db
            from app.services.response_cache import invalidate_cached
            # Document set changed → reports, training paths, and impacted users'
            # learning data are all stale.
            invalidate_cached('resp:report:', 'resp:paths:')
            try:
                # Archive all previous versions of this document and perform atomic flip of is_latest
                db.query(Document).filter(
                    Document.code == updated_doc.code,
                    Document.id != updated_doc.id
                ).update({
                    'status': 'archived',
                    'is_latest': False
                }, synchronize_session=False)

                updated_doc.is_latest = True
                db.add(updated_doc)

                from app.models.training import TrainingAssignment
                from app.services.notification_service import NotificationService

                notif_svc = NotificationService(db)
                from app.models.user_progress import UserProgress
                user_ids = self._collect_publish_impact_details(document_id)['user_ids']
                for uid in user_ids:
                    assignment = db.query(TrainingAssignment).filter(
                        TrainingAssignment.user_id == uid,
                        TrainingAssignment.document_id == document_id
                    ).first()
                    
                    if assignment:
                        assignment.status = "assigned"
                        assignment.verified_by_trainer = False
                        db.add(assignment)
                    else:
                        new_assign = TrainingAssignment(
                            user_id=uid,
                            document_id=document_id,
                            training_type="sop",
                            status="assigned"
                        )
                        db.add(new_assign)

                    # Sync to AnnexureX_SOPTraining
                    from app.models.annexure import AnnexureX_SOPTraining
                    from datetime import datetime, timezone
                    existing_sop = db.query(AnnexureX_SOPTraining).filter(
                        AnnexureX_SOPTraining.user_id == uid,
                        AnnexureX_SOPTraining.document_id == document_id,
                        AnnexureX_SOPTraining.result == 'pending'
                    ).first()
                    
                    trigger_reason = 'revision' if updated_doc.version > 1 else 'new_sop'
                    
                    if not existing_sop:
                        sop_rec = AnnexureX_SOPTraining(
                            user_id=uid,
                            document_id=document_id,
                            sop_code=updated_doc.code,
                            sop_title=updated_doc.title,
                            sop_version=updated_doc.version,
                            training_date=datetime.now(timezone.utc),
                            trigger_reason=trigger_reason,
                            result='pending'
                        )
                        db.add(sop_rec)

                    # Reset user progress record
                    progress = db.query(UserProgress).filter(
                        UserProgress.user_id == uid,
                        UserProgress.document_id == document_id
                    ).first()
                    if progress:
                        progress.completion_percentage = 0.0
                        progress.current_chunk_id = None
                        db.add(progress)

                    try:
                        notif_svc.notify_sop_updated([uid], updated_doc.title)
                    except Exception:
                        pass

                invalidate_cached(*(f'resp:learning:{uid}:' for uid in user_ids))
                db.commit()
            except Exception:
                db.rollback()

        return updated_doc

    def delete_document(self, document_id: int):
        document = self.repository.get_by_id(document_id)
        if not document:
            return False
            
        # Invalidate Semantic Cache before deleting
        from app.services.semantic_cache_service import SemanticCacheService
        try:
            cache = SemanticCacheService()
            cache.invalidate_document(document_id)
        except Exception:
            pass

        self.repository.delete(document)
        return True
