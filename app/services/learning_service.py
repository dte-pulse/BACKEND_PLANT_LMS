"""
LearningService — high-level learning domain orchestration.

Responsibilities:
  - List documents assigned to a trainee with enriched progress data.
  - Determine whether a trainee has completed the first-pass through a document
    (i.e., every chunk answered correctly at least once) — which unlocks review mode.
  - Provide a lightweight learning summary for dashboard widgets.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.chunk import Chunk
from app.models.training import TrainingAssignment
from app.models.user_progress import UserProgress

logger = logging.getLogger(__name__)


class LearningService:
    def __init__(self, db: Session):
        self.db = db

    # ─── Assigned document list ───────────────────────────────────────────────

    def get_assigned_documents(self, user_id: int) -> list[dict[str, Any]]:
        """Return all documents assigned to a trainee, enriched with progress data."""
        from app.models.document import Document

        assignments = (
            self.db.query(TrainingAssignment)
            .filter(
                TrainingAssignment.user_id == user_id,
                TrainingAssignment.status != 'revoked',
            )
            .all()
        )

        result = []
        for asgn in assignments:
            doc = self.db.query(Document).filter(Document.id == asgn.document_id).first()
            if not doc:
                continue

            progress = (
                self.db.query(UserProgress)
                .filter(
                    UserProgress.user_id == user_id,
                    UserProgress.document_id == asgn.document_id,
                )
                .first()
            )

            total_chunks = (
                self.db.query(Chunk)
                .filter(Chunk.document_id == asgn.document_id)
                .count()
            )

            completion_pct = progress.completion_percentage if progress else 0.0
            time_spent = progress.time_spent_seconds if progress else 0
            review_unlocked = completion_pct >= 100.0

            result.append({
                'assignment_id': asgn.id,
                'document_id': doc.id,
                'document_code': doc.code,
                'document_title': doc.title,
                'document_status': doc.status,
                'assignment_status': asgn.status,
                'total_chunks': total_chunks,
                'completion_percentage': round(completion_pct, 2),
                'time_spent_seconds': time_spent,
                'review_mode_unlocked': review_unlocked,
                'last_accessed_at': progress.last_accessed_at.isoformat() if progress and progress.last_accessed_at else None,
            })

        return result

    # ─── First-pass / review mode ─────────────────────────────────────────────

    def is_first_pass_complete(self, user_id: int, document_id: int) -> bool:
        """Return True if the user has completed the first pass through all chunks."""
        progress = (
            self.db.query(UserProgress)
            .filter(
                UserProgress.user_id == user_id,
                UserProgress.document_id == document_id,
            )
            .first()
        )
        return bool(progress and progress.completion_percentage >= 100.0)

    def get_max_unlocked_chunk_index(self, user_id: int, document_id: int) -> int:
        """
        Return the highest chunk index a user is allowed to jump to during first pass.

        During first pass (completion < 100%), users may only navigate to chunks
        they have already completed or the immediately next one.
        Once first pass is complete, they can jump to any chunk (review mode).
        """
        if self.is_first_pass_complete(user_id, document_id):
            # Review mode — unrestricted navigation
            total = self.db.query(Chunk).filter(Chunk.document_id == document_id).count()
            return total - 1  # 0-indexed

        progress = (
            self.db.query(UserProgress)
            .filter(
                UserProgress.user_id == user_id,
                UserProgress.document_id == document_id,
            )
            .first()
        )
        if not progress or not progress.current_chunk_id:
            return 0  # Only first chunk unlocked

        # Find the index of the current (last completed) chunk
        chunks = (
            self.db.query(Chunk)
            .filter(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .all()
        )
        for idx, chunk in enumerate(chunks):
            if chunk.id == progress.current_chunk_id:
                return idx  # Can navigate up to and including this chunk

        return 0

    # ─── Dashboard summary ────────────────────────────────────────────────────

    def get_learning_summary(self, user_id: int) -> dict[str, Any]:
        """Lightweight progress summary for dashboard widgets."""
        assignments = (
            self.db.query(TrainingAssignment)
            .filter(TrainingAssignment.user_id == user_id)
            .all()
        )

        total_assigned = len(assignments)
        completed = sum(1 for a in assignments if a.status == 'completed')
        in_progress = sum(1 for a in assignments if a.status == 'in_progress')

        all_progress = (
            self.db.query(UserProgress)
            .filter(UserProgress.user_id == user_id)
            .all()
        )
        total_time_seconds = sum(p.time_spent_seconds for p in all_progress)

        avg_completion = (
            sum(p.completion_percentage for p in all_progress) / len(all_progress)
            if all_progress else 0.0
        )

        return {
            'total_assigned': total_assigned,
            'completed': completed,
            'in_progress': in_progress,
            'not_started': total_assigned - completed - in_progress,
            'avg_completion_percentage': round(avg_completion, 2),
            'total_time_spent_seconds': total_time_seconds,
        }
