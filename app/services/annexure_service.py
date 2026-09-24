"""
Phase 7 — AnnexureService: generate all 11 compliance forms as printable HTML.
Also manages CRUD for AnnexureV TrainingRecord (individual employee record).
"""
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.training import TrainingAssignment
from app.models.document import Document


class AnnexureService:
    def __init__(self, db: Session):
        self.db = db

    # ─── Shared HTML helpers ─────────────────────────────────────────────────

    def _header(self, title: str, extra_css: str = '') -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; font-size: 13px; color: #111; }}
    h1 {{ text-align: center; font-size: 18px; margin-bottom: 4px; }}
    h2 {{ font-size: 14px; margin: 20px 0 8px; }}
    p {{ margin: 4px 0; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #e8e8e8; font-weight: bold; }}
    .meta {{ margin-bottom: 16px; }}
    .sigs {{ display: flex; justify-content: space-between; margin-top: 60px; }}
    .sig-box {{ text-align: center; }}
    .sig-line {{ border-top: 1px solid #333; width: 180px; padding-top: 4px; margin: 0 auto; }}
    @media print {{ body {{ margin: 20px; }} }}
    {extra_css}
  </style>
</head>
<body>
<h1>PULSE LMS — {title}</h1>
<p style="text-align:center;font-size:11px;color:#555;">Pharmaceutical Training Compliance System</p>
<hr>
"""

    def _footer(self, approver1: str = 'Trainee', approver2: str = 'HOD / Manager', approver3: str = 'QA Representative') -> str:
        return f"""
<div class="sigs">
  <div class="sig-box"><div class="sig-line"></div><p>{approver1} Signature &amp; Date</p></div>
  <div class="sig-box"><div class="sig-line"></div><p>{approver2} Signature &amp; Date</p></div>
  <div class="sig-box"><div class="sig-line"></div><p>{approver3} Signature &amp; Date</p></div>
</div>
</body></html>"""

    def _get_user(self, user_id: int) -> User:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(f'User {user_id} not found')
        return user

    # ─── Annexure I — Induction Schedule ────────────────────────────────────

    def generate_induction_schedule(self, user_id: int) -> str:
        user = self._get_user(user_id)
        assignments = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id == user_id,
            TrainingAssignment.training_type == 'induction',
        ).all()
        # N+1 fix: batch-fetch documents once.
        doc_ids = {a.document_id for a in assignments if a.document_id}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
        rows = ''
        for i, a in enumerate(assignments, 1):
            doc = docs.get(a.document_id)
            rows += f'<tr><td>{i}</td><td>{doc.code if doc else "—"}</td><td>{doc.title if doc else "—"}</td><td>{a.status.upper()}</td><td>&nbsp;</td></tr>'
        html = self._header('Annexure-I: Induction Training Schedule')
        html += f"""
<div class="meta">
  <p><strong>Employee Name:</strong> {user.full_name}</p>
  <p><strong>Employee Code:</strong> {user.employee_code}</p>
  <p><strong>Department:</strong> {user.department or '—'}</p>
  <p><strong>Date Generated:</strong> {datetime.now().strftime('%d-%b-%Y')}</p>
</div>
<table>
  <tr><th>#</th><th>SOP Code</th><th>Topic / SOP Title</th><th>Status</th><th>Completion Date</th></tr>
  {rows if rows else '<tr><td colspan="5" style="text-align:center;">No induction training found.</td></tr>'}
</table>"""
        html += self._footer()
        return html

    # ─── Annexure II — Induction Evaluation ─────────────────────────────────

    def generate_induction_evaluation(self, user_id: int) -> str:
        user = self._get_user(user_id)
        from app.models.user_mcq_attempt import UserMcqAttempt
        attempts = self.db.query(UserMcqAttempt).filter(
            UserMcqAttempt.user_id == user_id
        ).order_by(UserMcqAttempt.id.desc()).limit(10).all()

        # N+1 fix: batch-fetch documents once.
        doc_ids = {a.document_id for a in attempts if a.document_id}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
        rows = ''
        for a in attempts:
            doc = docs.get(a.document_id)
            rows += f'<tr><td>{doc.code if doc else "—"}</td><td>{doc.title if doc else "—"}</td><td>{a.score:.1f}%</td><td>{"PASS" if a.passed else "FAIL"}</td></tr>'

        html = self._header('Annexure-II: Induction Training Evaluation')
        html += f"""
<div class="meta">
  <p><strong>Employee Name:</strong> {user.full_name} &nbsp;&nbsp; <strong>Code:</strong> {user.employee_code}</p>
  <p><strong>Department:</strong> {user.department or '—'} &nbsp;&nbsp; <strong>Date:</strong> {datetime.now().strftime('%d-%b-%Y')}</p>
</div>
<table>
  <tr><th>SOP Code</th><th>SOP Title</th><th>Score</th><th>Result</th></tr>
  {rows if rows else '<tr><td colspan="4" style="text-align:center;">No assessments found.</td></tr>'}
</table>"""
        html += self._footer('Trainee', 'Trainer', 'HOD')
        return html

    # ─── Annexure III — Training Calendar ───────────────────────────────────

    def generate_calendar_report(self, year: int, department: str = '') -> str:
        from app.models.calendar import CalendarEvent
        events = self.db.query(CalendarEvent).all()
        rows = ''
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        for e in events:
            rows += f'<tr><td>{e.title}</td><td>{e.department or "All"}</td><td>{e.start_date.strftime("%d-%b-%Y") if e.start_date else "—"}</td><td>{e.end_date.strftime("%d-%b-%Y") if e.end_date else "—"}</td><td>{e.status or "planned"}</td></tr>'

        html = self._header(f'Annexure-III: Annual Training Calendar {year}')
        html += f"""
<div class="meta"><p><strong>Year:</strong> {year} &nbsp;&nbsp; <strong>Department:</strong> {department or 'All'}</p>
<p><strong>Generated:</strong> {datetime.now().strftime('%d-%b-%Y')}</p></div>
<table>
  <tr><th>Training Topic</th><th>Department</th><th>Planned Start</th><th>Planned End</th><th>Status</th></tr>
  {rows if rows else '<tr><td colspan="5" style="text-align:center;">No calendar entries found.</td></tr>'}
</table>"""
        html += self._footer('HOD', 'QA Head', 'Plant Head')
        return html

    # ─── Annexure IV — Attendance Sheet ─────────────────────────────────────

    def generate_attendance_sheet(self, event_id: int) -> str:
        from app.models.attendance import Attendance
        from app.models.calendar import CalendarEvent
        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
        if not event:
            raise ValueError(f'Calendar event {event_id} not found')
        records = self.db.query(Attendance).filter(Attendance.event_id == event_id).all()
        # N+1 fix: batch-fetch users once.
        # Note: intentionally tolerant of deleted users — renders "—" instead of
        # raising (previous _get_user() call raised 404 for orphaned rows).
        user_ids = {r.user_id for r in records if r.user_id}
        users = {u.id: u for u in self.db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
        rows = ''
        for i, r in enumerate(records, 1):
            user = users.get(r.user_id)
            rows += f'<tr><td>{i}</td><td>{user.employee_code if user else "—"}</td><td>{user.full_name if user else "—"}</td><td>{user.department or "—" if user else "—"}</td><td>{"✓" if r.attended else "✗"}</td><td>&nbsp;</td></tr>'

        html = self._header('Annexure-IV: Training Attendance Sheet')
        html += f"""
<div class="meta">
  <p><strong>Training Title:</strong> {event.title}</p>
  <p><strong>Date:</strong> {event.start_date.strftime('%d-%b-%Y') if event.start_date else '—'}</p>
  <p><strong>Department:</strong> {event.department or 'All'}</p>
</div>
<table>
  <tr><th>#</th><th>Emp. Code</th><th>Name</th><th>Department</th><th>Present</th><th>Signature</th></tr>
  {rows if rows else '<tr><td colspan="6" style="text-align:center;">No attendance records.</td></tr>'}
</table>"""
        html += self._footer('Trainer', 'HOD', 'QA')
        return html

    # ─── Annexure V — Individual Training Record ─────────────────────────────

    def generate_user_annexure(self, user_id: int) -> str:
        """Full individual training record — called from existing download endpoint."""
        user = self._get_user(user_id)
        assignments = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id == user_id,
        ).order_by(TrainingAssignment.created_at.desc()).all()

        from app.models.user_mcq_attempt import UserMcqAttempt
        # N+1 fix: batch-fetch documents + best attempt per document.
        doc_ids = {a.document_id for a in assignments if a.document_id}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
        best_by_doc: dict[int, UserMcqAttempt] = {}
        if doc_ids:
            for att in self.db.query(UserMcqAttempt).filter(
                UserMcqAttempt.user_id == user_id,
                UserMcqAttempt.document_id.in_(doc_ids),
            ).all():
                current = best_by_doc.get(att.document_id)
                if current is None or (att.score or 0) > (current.score or 0):
                    best_by_doc[att.document_id] = att
        rows = ''
        for a in assignments:
            doc = docs.get(a.document_id)
            best = best_by_doc.get(a.document_id) if a.document_id else None
            rows += f"""<tr>
              <td>{a.training_type.upper()}</td>
              <td>{doc.code if doc else '—'}</td>
              <td>{doc.title if doc else '—'}</td>
              <td>{a.status.replace('_', ' ').upper()}</td>
              <td>{f"{best.score:.1f}%" if best else '—'}</td>
              <td>{'PASS' if best and best.passed else ('FAIL' if best else '—')}</td>
              <td>{'✓' if a.verified_by_trainer else '—'}</td>
              <td>{a.created_at.strftime('%d-%b-%Y')}</td>
            </tr>"""

        html = self._header('Annexure-V: Individual Employee Training Record')
        html += f"""
<div class="meta">
  <p><strong>Employee Name:</strong> {user.full_name} &nbsp;&nbsp; <strong>Code:</strong> {user.employee_code}</p>
  <p><strong>Department:</strong> {user.department or '—'} &nbsp;&nbsp; <strong>Role:</strong> {user.role.value.upper()}</p>
  <p><strong>Date Generated:</strong> {datetime.now().strftime('%d-%b-%Y %H:%M')}</p>
</div>
<table>
  <tr><th>Type</th><th>Code</th><th>Title</th><th>Status</th><th>Score</th><th>Result</th><th>Verified</th><th>Date</th></tr>
  {rows if rows else '<tr><td colspan="8" style="text-align:center;">No training records found.</td></tr>'}
</table>"""
        html += self._footer()
        return html

    # ─── Annexure XI — cGMP Refresher ───────────────────────────────────────

    def generate_cgmp_report(self, year: int, department: str = '') -> str:
        from app.models.user import User as UserModel
        users = self.db.query(UserModel).filter(UserModel.is_active == True)
        if department:
            users = users.filter(UserModel.department == department)
        users = users.all()

        # N+1 fix: fetch all cGMP assignments for these users in ONE query,
        # then keep the latest per user by (created_at, id) in memory — same
        # result as the old order_by(created_at.desc()).first() per user.
        user_ids = [u.id for u in users]
        latest_cgmp: dict[int, TrainingAssignment] = {}
        if user_ids:
            for cgmp in self.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id.in_(user_ids),
                TrainingAssignment.training_type == 'cgmp',
            ).all():
                current = latest_cgmp.get(cgmp.user_id)
                if current is None or (cgmp.created_at, cgmp.id) > (current.created_at, current.id):
                    latest_cgmp[cgmp.user_id] = cgmp

        rows = ''
        for user in users:
            cgmp = latest_cgmp.get(user.id)
            rows += f'<tr><td>{user.employee_code}</td><td>{user.full_name}</td><td>{user.department or "—"}</td><td>{"Yes" if cgmp else "No"}</td><td>{cgmp.created_at.strftime("%d-%b-%Y") if cgmp else "—"}</td><td>{cgmp.status.upper() if cgmp else "NOT ASSIGNED"}</td></tr>'

        html = self._header(f'Annexure-XI: cGMP Refresher Training Record {year}')
        html += f"""
<div class="meta"><p><strong>Year:</strong> {year} &nbsp;&nbsp; <strong>Department:</strong> {department or 'All'}</p></div>
<table>
  <tr><th>Emp. Code</th><th>Name</th><th>Department</th><th>Completed</th><th>Date</th><th>Status</th></tr>
  {rows if rows else '<tr><td colspan="6" style="text-align:center;">No records found.</td></tr>'}
</table>"""
        html += self._footer('Employee', 'HOD', 'QA Head')
        return html

    # ─── Annexure VI — Trainer Qualification ────────────────────────────────
    def generate_trainer_qualification(self, record_id: int) -> str:
        from app.models.annexure import AnnexureVI_TrainerQualification
        record = self.db.query(AnnexureVI_TrainerQualification).filter(AnnexureVI_TrainerQualification.id == record_id).first()
        if not record:
            raise ValueError('Record not found')
        user = self._get_user(record.trainer_id)
        html = self._header('Annexure-VI: Trainer Qualification Record')
        html += f"""
<div class="meta">
  <p><strong>Trainer Name:</strong> {user.full_name}</p>
  <p><strong>Employee Code:</strong> {user.employee_code}</p>
  <p><strong>Subject Area:</strong> {record.subject_area}</p>
  <p><strong>Qualification Date:</strong> {record.qualification_date.strftime('%d-%b-%Y') if record.qualification_date else '—'}</p>
  <p><strong>Validity Years:</strong> {record.validity_years} years &nbsp;&nbsp; <strong>Expiry Date:</strong> {record.expiry_date.strftime('%d-%b-%Y') if record.expiry_date else '—'}</p>
  <p><strong>Status:</strong> {record.status or 'Active'}</p>
  <p><strong>Remarks:</strong> {record.remarks or '—'}</p>
</div>"""
        html += self._footer('Trainer', 'Evaluator', 'QA Head')
        return html

    # ─── Annexure VII — Need-Based Training Request ──────────────────────────
    def generate_need_based_training(self, record_id: int) -> str:
        from app.models.annexure import AnnexureVII_NeedBasedTraining
        record = self.db.query(AnnexureVII_NeedBasedTraining).filter(AnnexureVII_NeedBasedTraining.id == record_id).first()
        if not record:
            raise ValueError('Record not found')
        user = self._get_user(record.requester_id)
        html = self._header('Annexure-VII: Need-Based Training Request')
        html += f"""
<div class="meta">
  <p><strong>Requester Name:</strong> {user.full_name} &nbsp;&nbsp; <strong>Code:</strong> {user.employee_code}</p>
  <p><strong>Department:</strong> {record.department}</p>
  <p><strong>Requested Date:</strong> {record.requested_date.strftime('%d-%b-%Y') if record.requested_date else '—'}</p>
  <p><strong>Status:</strong> {record.status or 'Pending'}</p>
</div>
<div style="margin-top: 16px;">
  <p><strong>Training Need / Subject:</strong></p>
  <blockquote style="background:#f9f9f9; border-left:4px solid #ccc; padding:10px; margin: 8px 0;">{record.training_need}</blockquote>
  <p><strong>Justification / Reason:</strong></p>
  <blockquote style="background:#f9f9f9; border-left:4px solid #ccc; padding:10px; margin: 8px 0;">{record.reason}</blockquote>
  <p><strong>Target Employees:</strong></p>
  <p>{record.target_employees or '—'}</p>
  <p><strong>Approval Remarks:</strong></p>
  <p>{record.approval_remarks or '—'}</p>
</div>"""
        html += self._footer('Requester', 'Department Head', 'QA Head')
        return html

    # ─── Annexure VIII — External Training Record ────────────────────────────
    def generate_external_training(self, record_id: int) -> str:
        from app.models.annexure import AnnexureVIII_ExternalTraining
        record = self.db.query(AnnexureVIII_ExternalTraining).filter(AnnexureVIII_ExternalTraining.id == record_id).first()
        if not record:
            raise ValueError('Record not found')
        user = self._get_user(record.user_id)
        html = self._header('Annexure-VIII: External Training Record')
        html += f"""
<div class="meta">
  <p><strong>Trainee Name:</strong> {user.full_name} &nbsp;&nbsp; <strong>Code:</strong> {user.employee_code}</p>
  <p><strong>Training Title:</strong> {record.training_title}</p>
  <p><strong>Agency Name:</strong> {record.agency_name}</p>
  <p><strong>Venue:</strong> {record.venue or '—'}</p>
  <p><strong>Dates:</strong> {record.start_date.strftime('%d-%b-%Y') if record.start_date else '—'} to {record.end_date.strftime('%d-%b-%Y') if record.end_date else '—'} ({record.duration_days or 0} days)</p>
  <p><strong>Cost:</strong> ${record.cost or 0.00}</p>
  <p><strong>Certificate Attachment:</strong> {record.certificate_url or '—'}</p>
</div>
<div style="margin-top: 16px;">
  <p><strong>Brief Summary of Learning:</strong></p>
  <blockquote style="background:#f9f9f9; border-left:4px solid #ccc; padding:10px; margin: 8px 0;">{record.learning_summary or '—'}</blockquote>
</div>"""
        html += self._footer('Trainee', 'Trainer / Coordinator', 'HOD')
        return html

    # ─── Annexure IX — On-the-Job (OJT) Record ───────────────────────────────
    def generate_ojt_record(self, record_id: int) -> str:
        from app.models.annexure import AnnexureIX_OJTRecord
        record = self.db.query(AnnexureIX_OJTRecord).filter(AnnexureIX_OJTRecord.id == record_id).first()
        if not record:
            raise ValueError('Record not found')
        user = self._get_user(record.user_id)
        trainer = self._get_user(record.trainer_id) if record.trainer_id else None
        html = self._header('Annexure-IX: On-the-Job (OJT) Training Record')
        html += f"""
<div class="meta">
  <p><strong>Trainee Name:</strong> {user.full_name} &nbsp;&nbsp; <strong>Code:</strong> {user.employee_code}</p>
  <p><strong>Trainer Name:</strong> {trainer.full_name if trainer else '—'}</p>
  <p><strong>OJT Topic:</strong> {record.topic_title}</p>
  <p><strong>Duration:</strong> {record.start_date.strftime('%d-%b-%Y') if record.start_date else '—'} to {record.end_date.strftime('%d-%b-%Y') if record.end_date else '—'}</p>
</div>
<table style="margin-top:20px;">
  <tr><th>Assessment Aspect</th><th>Score / Details</th></tr>
  <tr><td>Tasks Performed / Practical Steps</td><td>{record.tasks_performed or '—'}</td></tr>
  <tr><td>Trainer Observations / Evaluation</td><td>{record.trainer_observation or '—'}</td></tr>
  <tr><td>Practical Score</td><td>{record.practical_score}%</td></tr>
  <tr><td>Written Test Score</td><td>{record.written_test_score}%</td></tr>
  <tr style="font-weight:bold;"><td>Result Status</td><td>{record.result or '—'}</td></tr>
</table>"""
        html += self._footer('Trainee', 'Trainer', 'HOD')
        return html

    # ─── Annexure X — SOP Training Record ───────────────────────────────────
    def generate_sop_training(self, record_id: int) -> str:
        from app.models.annexure import AnnexureX_SOPTraining
        record = self.db.query(AnnexureX_SOPTraining).filter(AnnexureX_SOPTraining.id == record_id).first()
        if not record:
            raise ValueError('Record not found')
        user = self._get_user(record.user_id)
        trainer = self._get_user(record.trainer_id) if record.trainer_id else None
        html = self._header('Annexure-X: SOP Training Record')
        html += f"""
<div class="meta">
  <p><strong>Trainee Name:</strong> {user.full_name} &nbsp;&nbsp; <strong>Code:</strong> {user.employee_code}</p>
  <p><strong>Trainer Name:</strong> {trainer.full_name if trainer else '—'}</p>
  <p><strong>SOP Reference:</strong> {record.sop_code} v{record.sop_version} — {record.sop_title}</p>
  <p><strong>Training Date:</strong> {record.training_date.strftime('%d-%b-%Y') if record.training_date else '—'}</p>
  <p><strong>Trigger / Mandate Reason:</strong> {record.trigger_reason or '—'}</p>
  <p><strong>Evaluation Score:</strong> {record.score}%</p>
  <p><strong>Result:</strong> {record.result or '—'}</p>
</div>"""
        html += self._footer('Trainee', 'Trainer', 'QA / HOD')
        return html
