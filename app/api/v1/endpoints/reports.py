from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.report_service import ReportService

router = APIRouter(prefix='/reports', tags=['reports'])


def get_report_service(db: Session = Depends(get_db)):
    return ReportService(db)


@router.get('/global-readiness')
def get_global_readiness(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    return service.get_global_readiness()


@router.get('/compliance')
def get_compliance_report(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    """Department-by-department compliance breakdown."""
    return service.get_compliance_report()


@router.get('/overdue')
def get_overdue_report(
    department: str | None = None,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    """List all overdue training assignments sorted by days overdue."""
    return service.get_overdue_report(department=department)


@router.get('/nq-employees')
def get_nq_employees(
    department: str | None = None,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    """Employees with critical weak areas (repeated NQ status)."""
    return service.get_nq_employees(department=department)


@router.get('/token-usage')
def get_token_usage(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_role([UserRole.admin])),
    service: ReportService = Depends(get_report_service),
):
    """LLM token consumption and cost report for the given period."""
    return service.get_token_usage_report(days=days)


@router.get('/department-compliance/{department_name}')
def get_department_compliance(
    department_name: str,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    return service.get_department_compliance(department_name)


@router.get('/user-history/{user_id}')
def get_user_history(
    user_id: int,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    return service.get_user_training_history(user_id)


import csv
import io
from fastapi.responses import StreamingResponse

@router.get('/compliance/export')
def export_compliance_report(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    """CSV Export of department-by-department compliance breakdown."""
    data = service.get_compliance_report()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Department', 'Total Employees', 'Total Assignments', 'Completed', 'Overdue', 'NQ Employees', 'Compliance Score'])
    for d in data:
        writer.writerow([
            d['department'],
            d['total_employees'],
            d['total_assignments'],
            d['completed'],
            d['overdue'],
            d['nq_employees'],
            d['compliance_score']
        ])
    
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type='text/csv',
        headers={"Content-Disposition": "attachment; filename=compliance_report.csv"}
    )


@router.get('/overdue/export')
def export_overdue_report(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: ReportService = Depends(get_report_service),
):
    """CSV Export of overdue training assignments."""
    data = service.get_overdue_report()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Assignment ID', 'User ID', 'Employee Code', 'Full Name', 'Department', 'Training Type', 'Document Code', 'Document Title', 'Due Date', 'Days Overdue', 'Status'])
    for d in data:
        writer.writerow([
            d['assignment_id'],
            d['user_id'],
            d['employee_code'],
            d['full_name'],
            d['department'],
            d['training_type'],
            d['document_code'],
            d['document_title'],
            d['due_date'].strftime('%Y-%m-%d') if d['due_date'] else '',
            d['days_overdue'],
            d['status']
        ])
    
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type='text/csv',
        headers={"Content-Disposition": "attachment; filename=overdue_report.csv"}
    )


@router.get('/annexures/{type}')
def get_annexure_records(
    type: str,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    db: Session = Depends(get_db)
):
    """Retrieve all database records for a specific annexure type (i through xi)."""
    from app.models.annexure import (
        AnnexureI_InductionSchedule,
        AnnexureII_InductionEvaluation,
        AnnexureIII_TrainingCalendar,
        AnnexureIV_AttendanceSheet,
        AnnexureV_TrainingRecord,
        AnnexureVI_TrainerQualification,
        AnnexureVII_NeedBasedTraining,
        AnnexureVIII_ExternalTraining,
        AnnexureIX_OJTRecord,
        AnnexureX_SOPTraining,
        AnnexureXI_CGMPRefresher
    )
    
    mapping = {
        'i': AnnexureI_InductionSchedule,
        'induction-schedule': AnnexureI_InductionSchedule,
        'ii': AnnexureII_InductionEvaluation,
        'induction-evaluation': AnnexureII_InductionEvaluation,
        'iii': AnnexureIII_TrainingCalendar,
        'training-calendar': AnnexureIII_TrainingCalendar,
        'iv': AnnexureIV_AttendanceSheet,
        'attendance-sheet': AnnexureIV_AttendanceSheet,
        'v': AnnexureV_TrainingRecord,
        'training-record': AnnexureV_TrainingRecord,
        'vi': AnnexureVI_TrainerQualification,
        'trainer-qualification': AnnexureVI_TrainerQualification,
        'vii': AnnexureVII_NeedBasedTraining,
        'need-based-training': AnnexureVII_NeedBasedTraining,
        'viii': AnnexureVIII_ExternalTraining,
        'external-training': AnnexureVIII_ExternalTraining,
        'ix': AnnexureIX_OJTRecord,
        'ojt-record': AnnexureIX_OJTRecord,
        'x': AnnexureX_SOPTraining,
        'sop-training': AnnexureX_SOPTraining,
        'xi': AnnexureXI_CGMPRefresher,
        'cgmp-refresher': AnnexureXI_CGMPRefresher
    }
    
    model_class = mapping.get(type.lower())
    if not model_class:
        raise HTTPException(status_code=400, detail=f"Invalid annexure type: {type}")
        
    records = db.query(model_class).all()
    serialized = []
    for r in records:
        d = {col.name: getattr(r, col.name) for col in r.__table__.columns}
        # format datetimes as iso strings
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        serialized.append(d)
    return serialized


@router.get('/annexures/{type}/export')
def export_annexure_records(
    type: str,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    db: Session = Depends(get_db)
):
    """CSV Export for a specific annexure type (i through xi)."""
    data = get_annexure_records(type=type, current_user=current_user, db=db)
    if not data:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['No records found'])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8')),
            media_type='text/csv',
            headers={"Content-Disposition": f"attachment; filename=annexure_{type}.csv"}
        )
        
    output = io.StringIO()
    writer = csv.writer(output)
    headers = list(data[0].keys())
    writer.writerow(headers)
    for row in data:
        writer.writerow([row.get(h) for h in headers])
        
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type='text/csv',
        headers={"Content-Disposition": f"attachment; filename=annexure_{type}.csv"}
    )
