"""
All 11 SOP Annexure models for the Pulse LMS.
Each Annexure maps to a digitized compliance form.
"""
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AnnexureI_InductionSchedule(Base):
    """Annexure-I: Induction Training Schedule"""
    __tablename__ = 'annexure_induction_schedule'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    joining_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_start: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    topics_covered: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    status: Mapped[str] = mapped_column(String(30), default='planned')  # planned, in_progress, completed
    hod_approved: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureII_InductionEvaluation(Base):
    """Annexure-II: Induction Training Evaluation"""
    __tablename__ = 'annexure_induction_evaluation'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey('annexure_induction_schedule.id'), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    evaluation_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    theory_score: Mapped[float] = mapped_column(Float, default=0.0)
    practical_score: Mapped[float] = mapped_column(Float, default=0.0)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str] = mapped_column(String(20), default='pending')  # qualified, not_qualified, pending
    trainer_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    hod_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureIII_TrainingCalendar(Base):
    """Annexure-III: Department Annual Training Calendar"""
    __tablename__ = 'annexure_training_calendar'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    training_type: Mapped[str] = mapped_column(String(50), nullable=False)
    topic_title: Mapped[str] = mapped_column(String(255), nullable=False)
    planned_month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-12
    planned_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    trainer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    duration_hours: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(30), default='planned')  # planned, completed, deferred, carry_forward
    carry_forward_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hod_approved: Mapped[bool] = mapped_column(default=False)
    qa_approved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureIV_AttendanceSheet(Base):
    """Annexure-IV: Training Attendance Sheet"""
    __tablename__ = 'annexure_attendance_sheet'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    calendar_id: Mapped[int | None] = mapped_column(ForeignKey('annexure_training_calendar.id'), nullable=True)
    training_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    topic_title: Mapped[str] = mapped_column(String(255), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trainer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    total_invitees: Mapped[int] = mapped_column(Integer, default=0)
    total_present: Mapped[int] = mapped_column(Integer, default=0)
    attendance_pct: Mapped[float] = mapped_column(Float, default=0.0)
    below_threshold: Mapped[bool] = mapped_column(default=False)  # True if < 70%
    rescheduled: Mapped[bool] = mapped_column(default=False)
    material_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureV_TrainingRecord(Base):
    """Annexure-V: Individual Employee Training Record"""
    __tablename__ = 'annexure_training_record'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    training_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    topic_title: Mapped[str] = mapped_column(String(255), nullable=False)
    training_type: Mapped[str] = mapped_column(String(50), nullable=False)  # induction, ojt, sop, etc.
    document_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trainer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str] = mapped_column(String(20), default='pending')
    certificate_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureVI_TrainerQualification(Base):
    """Annexure-VI: Trainer Qualification Record"""
    __tablename__ = 'annexure_trainer_qualification'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    subject_area: Mapped[str] = mapped_column(String(255), nullable=False)
    qualification_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    validity_years: Mapped[int] = mapped_column(Integer, default=2)
    expiry_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='active')  # active, expired, revoked
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureVII_NeedBasedTraining(Base):
    """Annexure-VII: Need-Based Training Request"""
    __tablename__ = 'annexure_need_based_training'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    training_need: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    target_employees: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of user IDs
    requested_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='pending')  # pending, approved, rejected, completed
    hod_approved: Mapped[bool] = mapped_column(default=False)
    qa_approved: Mapped[bool] = mapped_column(default=False)
    approval_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureVIII_ExternalTraining(Base):
    """Annexure-VIII: External Training Record"""
    __tablename__ = 'annexure_external_training'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    training_title: Mapped[str] = mapped_column(String(255), nullable=False)
    agency_name: Mapped[str] = mapped_column(String(255), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_days: Mapped[float] = mapped_column(Float, default=1.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    certificate_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hod_approved: Mapped[bool] = mapped_column(default=False)
    hr_archived: Mapped[bool] = mapped_column(default=False)
    learning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureIX_OJTRecord(Base):
    """Annexure-IX: On-the-Job Training (OJT) Record"""
    __tablename__ = 'annexure_ojt_record'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey('topics.id'), nullable=True)
    topic_title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    tasks_performed: Mapped[str | None] = mapped_column(Text, nullable=True)
    trainer_observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    practical_score: Mapped[float] = mapped_column(Float, default=0.0)
    written_test_score: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str] = mapped_column(String(20), default='pending')
    work_allotted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureX_SOPTraining(Base):
    """Annexure-X: SOP Training Record"""
    __tablename__ = 'annexure_sop_training'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey('documents.id'), nullable=True)
    sop_code: Mapped[str] = mapped_column(String(100), nullable=False)
    sop_title: Mapped[str] = mapped_column(String(255), nullable=False)
    sop_version: Mapped[int] = mapped_column(Integer, default=1)
    training_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger_reason: Mapped[str] = mapped_column(String(50), default='new_sop')  # new_sop, revision, periodic
    score: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str] = mapped_column(String(20), default='pending')
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnexureXI_CGMPRefresher(Base):
    """Annexure-XI: cGMP Refresher Training Record"""
    __tablename__ = 'annexure_cgmp_refresher'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    training_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    topics_covered: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_gap_identified: Mapped[bool] = mapped_column(default=False)
    skill_gap_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str] = mapped_column(String(20), default='pending')
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentAssignment(Base):
    """Maps documents to users or departments for training access."""
    __tablename__ = 'document_assignments'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)  # dept-level assignment
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    due_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    is_mandatory: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
