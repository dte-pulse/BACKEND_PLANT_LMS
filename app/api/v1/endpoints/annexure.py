from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.annexure_service import AnnexureService

router = APIRouter(prefix='/annexure', tags=['annexure'])


def get_annexure_service(db: Session = Depends(get_db)):
    return AnnexureService(db)


@router.get('/user/{user_id}/training-record', response_class=HTMLResponse)
def download_training_record(
    user_id: int,
    current_user: User = Depends(get_current_user),
    service: AnnexureService = Depends(get_annexure_service),
):
    """Annexure-V: Individual employee training record."""
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    try:
        return HTMLResponse(content=service.generate_user_annexure(user_id))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# Keep backwards compat
@router.get('/{user_id}/download', response_class=HTMLResponse)
def download_annexure(
    user_id: int,
    current_user: User = Depends(get_current_user),
    service: AnnexureService = Depends(get_annexure_service),
):
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    try:
        return HTMLResponse(content=service.generate_user_annexure(user_id))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get('/i/induction-schedule/{user_id}', response_class=HTMLResponse)
def annexure_i(
    user_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    """Annexure-I: Induction Training Schedule."""
    try:
        return HTMLResponse(content=service.generate_induction_schedule(user_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/ii/induction-evaluation/{user_id}', response_class=HTMLResponse)
def annexure_ii(
    user_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    """Annexure-II: Induction Training Evaluation."""
    try:
        return HTMLResponse(content=service.generate_induction_evaluation(user_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/iii/training-calendar', response_class=HTMLResponse)
def annexure_iii(
    year: int = Query(default=2025),
    department: str = Query(default=''),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: AnnexureService = Depends(get_annexure_service),
):
    """Annexure-III: Annual Training Calendar."""
    return HTMLResponse(content=service.generate_calendar_report(year, department))


@router.get('/iv/attendance-sheet/{event_id}', response_class=HTMLResponse)
def annexure_iv(
    event_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    """Annexure-IV: Training Attendance Sheet."""
    try:
        return HTMLResponse(content=service.generate_attendance_sheet(event_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/xi/cgmp-refresher', response_class=HTMLResponse)
def annexure_xi(
    year: int = Query(default=2025),
    department: str = Query(default=''),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: AnnexureService = Depends(get_annexure_service),
):
    """Annexure-XI: cGMP Refresher Training Record."""
    return HTMLResponse(content=service.generate_cgmp_report(year, department))


@router.get('/vi/trainer-qualification/{record_id}', response_class=HTMLResponse)
def annexure_vi(
    record_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    try:
        return HTMLResponse(content=service.generate_trainer_qualification(record_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/vii/need-based-training/{record_id}', response_class=HTMLResponse)
def annexure_vii(
    record_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    try:
        return HTMLResponse(content=service.generate_need_based_training(record_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/viii/external-training/{record_id}', response_class=HTMLResponse)
def annexure_viii(
    record_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    try:
        return HTMLResponse(content=service.generate_external_training(record_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/ix/ojt-record/{record_id}', response_class=HTMLResponse)
def annexure_ix(
    record_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    try:
        return HTMLResponse(content=service.generate_ojt_record(record_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/x/sop-training/{record_id}', response_class=HTMLResponse)
def annexure_x(
    record_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: AnnexureService = Depends(get_annexure_service),
):
    try:
        return HTMLResponse(content=service.generate_sop_training(record_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post('/{type}', status_code=status.HTTP_201_CREATED)
def submit_annexure_form(
    type: str,
    payload: dict,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    db: Session = Depends(get_db)
):
    """Generic POST endpoint to submit compliance form data for any of the 11 annexures (i through xi)."""
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
        
    try:
        model_fields = {col.name for col in model_class.__table__.columns if col.name != 'id' and col.name != 'created_at'}
        parsed_data = {k: v for k, v in payload.items() if k in model_fields}
        
        # parse datetime fields
        from sqlalchemy import DateTime as SqlaDateTime
        from datetime import datetime
        for col in model_class.__table__.columns:
            if isinstance(col.type, SqlaDateTime) and col.name in parsed_data:
                val = parsed_data[col.name]
                if isinstance(val, str) and val:
                    try:
                        parsed_data[col.name] = datetime.fromisoformat(val.replace('Z', '+00:00'))
                    except ValueError:
                        try:
                            parsed_data[col.name] = datetime.strptime(val, '%Y-%m-%d')
                        except ValueError:
                            pass
        
        record = model_class(**parsed_data)
        db.add(record)
        db.commit()
        db.refresh(record)
        
        # serialize
        res = {col.name: getattr(record, col.name) for col in record.__table__.columns}
        for k, v in res.items():
            if isinstance(v, datetime):
                res[k] = v.isoformat()
        return res
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to submit annexure form: {str(e)}")

