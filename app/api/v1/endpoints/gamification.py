"""Gamification endpoints — coins wallet, streak, leaderboard, history.

Leaderboard is trainee-only: it's a learner-motivation surface; staff roles
(admin/HOD/trainer) have their own reporting views instead.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.gamification_service import GamificationService

router = APIRouter(prefix='/gamification', tags=['gamification'])


def get_service(db: Session = Depends(get_db)) -> GamificationService:
    return GamificationService(db)


@router.get('/wallet')
def wallet(
    current_user: User = Depends(get_current_user),
    service: GamificationService = Depends(get_service),
):
    """Coin popup data: totals, this-month coins, global rank, breakdown."""
    return service.wallet(current_user.id)


@router.get('/streak')
def streak(
    current_user: User = Depends(get_current_user),
    service: GamificationService = Depends(get_service),
):
    """Streak popup data: current/longest streak + week strip."""
    from app.services.gamification_service import _today
    s = service._get_or_create_streak(current_user.id)
    d = service.streak_dict(s)
    d['today'] = _today().isoweekday()  # 1=Mon..7=Sun — lets the UI highlight today
    return d


@router.get('/leaderboard')
def leaderboard(
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000, le=2100),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(require_role([UserRole.trainee])),
    service: GamificationService = Depends(get_service),
):
    """Monthly coin leaderboard (resets each calendar month). Trainees only —
    staff competition would skew the board; they have report views instead."""
    try:
        return service.leaderboard(month=month, year=year, limit=limit,
                                   user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/history')
def history(
    limit: int = Query(default=30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    service: GamificationService = Depends(get_service),
):
    """Recent coin-earning events (Earning history view)."""
    return service.earning_history(current_user.id, limit=limit)
