"""Gamification service — coins, streaks and the monthly leaderboard.

Reward economy (kept deliberately simple and legible to learners):
  - Chunk finished          5 coins
  - MCQ passed             10 coins   (failed attempt: 1 consolation coin)
  - Training assignment    25 coins
  - Daily login (1st)       2 coins
  - PPWEC interaction      10   (mapped from the PPWEC point event)
  - PPWEC assessment pass  100
  - PPWEC challenge day     10   (+70 bonus on completion)
  - PPWEC module complete   25
  - Streak milestone        20 coins extra at every 7 consecutive days

Design notes:
  - The coin ledger is append-only; dedup happens by (user, activity_type,
    reference_type, reference_id) checked before insert and enforced by a
    unique index, so double-posts collapse to a no-op instead of double pay.
  - Streaks update on the UTC *calendar day* of the first coin-earning event.
  - Leaderboard ranks by coins earned *this calendar month* (resets monthly,
    like the reference UI), with an all-time column too.
"""
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models.gamification import CoinLedger, UserStreak
from app.models.user import User

logger = logging.getLogger(__name__)

# Coin economy (§V.3 spirit — small, legible, never pay-to-win)
COIN_RULES = {
    'chunk_complete': 5,
    'mcq_pass': 10,
    'mcq_fail': 1,           # consolation — keeps momentum without rewarding failure
    'assignment_complete': 25,
    'daily_login': 2,
    'ppwec_interaction': 10,
    'ppwec_assessment': 100,
    'ppwec_challenge_day': 10,
    'ppwec_challenge_bonus': 70,
    'ppwec_module_complete': 25,
    'streak_bonus': 20,      # every 7-day streak milestone
}
STREAK_MILESTONE = 7

# Activity-type groups shown in the wallet's "Coin Breakdown" cards
BREAKDOWN_GROUPS = {
    'daily': ['daily_login', 'streak_bonus'],
    'learning': ['chunk_complete', 'mcq_pass', 'mcq_fail', 'assignment_complete'],
    'practice': ['ppwec_interaction', 'ppwec_assessment', 'ppwec_challenge_day',
                 'ppwec_challenge_bonus', 'ppwec_module_complete'],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today() -> date:
    return _utcnow().date()


def _month_start(d: date | None = None) -> datetime:
    d = d or _today()
    return datetime(d.year, d.month, 1)


def _next_month_start(d: date | None = None) -> datetime:
    d = d or _today()
    if d.month == 12:
        return datetime(d.year + 1, 1, 1)
    return datetime(d.year, d.month + 1, 1)


def _weekday(d: date) -> int:
    """ISO weekday 1=Mon..7=Sun — matches the M..S strip in the UI."""
    return d.isoweekday()


class CoinAwarded(Exception):
    """Internal control-flow: dedup index rejected a repeat award."""


class GamificationService:
    def __init__(self, db: Session):
        self.db = db

    # ─── Awarding ────────────────────────────────────────────────────────────

    def award(
        self,
        user_id: int,
        activity_type: str,
        *,
        reference_type: str | None = None,
        reference_id: str | int | None = None,
        module_id: int | None = None,
        document_id: int | None = None,
        coins: int | None = None,
        meta: dict | None = None,
    ) -> int | None:
        """Credit coins for one activity. Returns coins awarded, or None when
        deduplicated (already awarded for this reference)."""
        rule = COIN_RULES.get(activity_type)
        amount = coins if coins is not None else (rule or 0)
        if amount <= 0:
            return None

        dedup_key = (
            (str(reference_id) if reference_id is not None else None)
            if reference_type else None
        )
        if reference_type and dedup_key is not None:
            existing = (
                self.db.query(CoinLedger)
                .filter(
                    CoinLedger.user_id == user_id,
                    CoinLedger.activity_type == activity_type,
                    CoinLedger.reference_type == reference_type,
                    CoinLedger.reference_id == dedup_key,
                )
                .first()
            )
            if existing:
                return None

        try:
            row = CoinLedger(
                user_id=user_id,
                activity_type=activity_type,
                points=amount,
                module_id=module_id,
                document_id=document_id,
                reference_type=reference_type,
                reference_id=dedup_key,
                meta=meta,
            )
            self.db.add(row)
            self.db.flush()
        except Exception as exc:  # unique index raced: treat as already-awarded
            self.db.rollback()
            logger.debug('Coin award deduplicated for user %s %s/%s: %s',
                         user_id, activity_type, dedup_key, exc)
            return None

        self._touch_streak(user_id, amount)
        return amount

    # ─── Streaks ─────────────────────────────────────────────────────────────

    def _touch_streak(self, user_id: int, coins: int) -> None:
        """Update streak state for a coin-earning event today."""
        streak = self._get_or_create_streak(user_id)
        today = _today()

        last = streak.last_active_date.date() if streak.last_active_date else None
        if last == today:
            # Already counted today — just accumulate coins.
            streak.total_coins = (streak.total_coins or 0) + coins
            self.db.flush()
            return

        if last == today - timedelta(days=1):
            streak.current_streak = (streak.current_streak or 0) + 1
        elif last is None or last < today - timedelta(days=1):
            streak.current_streak = 1

        streak.longest_streak = max(streak.longest_streak or 0, streak.current_streak)
        streak.last_active_date = datetime(today.year, today.month, today.day)

        # Week strip: ISO weekdays (1..7) active this week (Mon-based).
        week = set(streak.week_activity or [])
        # Reset the strip when the stored week is a previous ISO week.
        if week:
            days = [today - timedelta(days=i) for i in range(today.isoweekday() - 1)]
            week_days = {d for d in days if _weekday(d) in week}
            if not week_days and today.isoweekday() > 1:
                week = set()
        week.add(_weekday(today))
        streak.week_activity = sorted(week)

        streak.total_coins = (streak.total_coins or 0) + coins

        # Streak milestone bonus
        if streak.current_streak > 0 and streak.current_streak % STREAK_MILESTONE == 0:
            bonus = COIN_RULES['streak_bonus']
            self.db.add(CoinLedger(
                user_id=user_id,
                activity_type='streak_bonus',
                points=bonus,
                reference_type='streak',
                reference_id=f'{today.isoformat()}',
                meta={'milestone': streak.current_streak},
            ))
            streak.total_coins += bonus
            try:
                from app.services.notification_service import NotificationService
                NotificationService(self.db).publish(
                    user_id, 'Streak Milestone! 🔥',
                    f'{streak.current_streak}-day streak — +{bonus} bonus coins!',
                    'success',
                )
            except Exception:  # noqa: BLE001
                logger.debug('Streak notification failed', exc_info=True)

        self.db.flush()

    def _get_or_create_streak(self, user_id: int) -> UserStreak:
        streak = (
            self.db.query(UserStreak)
            .filter(UserStreak.user_id == user_id)
            .first()
        )
        if not streak:
            streak = UserStreak(user_id=user_id, current_streak=0, longest_streak=0,
                                total_coins=0, week_activity=[])
            self.db.add(streak)
            self.db.flush()
        return streak

    # ─── Wallet (coin popup) ────────────────────────────────────────────────

    def wallet(self, user_id: int) -> dict:
        """Everything the coin popup needs: totals, this-month coins, global
        rank, breakdown by group."""
        total = self.total_coins(user_id)
        month_coins = self.month_coins(user_id)
        rank = self.global_rank(user_id)

        by_type = dict(
            self.db.query(CoinLedger.activity_type, sa_func.coalesce(sa_func.sum(CoinLedger.points), 0))
            .filter(CoinLedger.user_id == user_id)
            .group_by(CoinLedger.activity_type)
            .all()
        )
        breakdown = {}
        for group, types in BREAKDOWN_GROUPS.items():
            breakdown[group] = sum(int(by_type.get(t, 0) or 0) for t in types)

        streak = self._get_or_create_streak(user_id)
        return {
            'total_coins': total,
            'month_coins': month_coins,
            'global_rank': rank,
            'college_rank': None,  # placeholder — single-tenant deployment
            'breakdown': breakdown,
            'streak': self.streak_dict(streak),
        }

    def streak_dict(self, streak: UserStreak) -> dict:
        return {
            'current_streak': streak.current_streak or 0,
            'longest_streak': streak.longest_streak or 0,
            'week_activity': streak.week_activity or [],
            'last_active_date': streak.last_active_date.date().isoformat() if streak.last_active_date else None,
        }

    def total_coins(self, user_id: int) -> int:
        return int(
            self.db.query(sa_func.coalesce(sa_func.sum(CoinLedger.points), 0))
            .filter(CoinLedger.user_id == user_id)
            .scalar() or 0
        )

    def month_coins(self, user_id: int, *, year: int | None = None, month: int | None = None) -> int:
        start = _month_start(date(year, month, 1) if year and month else None)
        end = _next_month_start(date(year, month, 1) if year and month else None)
        return int(
            self.db.query(sa_func.coalesce(sa_func.sum(CoinLedger.points), 0))
            .filter(
                CoinLedger.user_id == user_id,
                CoinLedger.created_at >= start,
                CoinLedger.created_at < end,
            )
            .scalar() or 0
        )

    def global_rank(self, user_id: int) -> int | None:
        """All-time rank by total coins (1 = richest)."""
        ranked = (
            self.db.query(CoinLedger.user_id, sa_func.sum(CoinLedger.points).label('c'))
            .group_by(CoinLedger.user_id)
            .order_by(sa_func.sum(CoinLedger.points).desc())
            .all()
        )
        for pos, (uid, _c) in enumerate(ranked, start=1):
            if uid == user_id:
                return pos
        return None

    # ─── Leaderboard ────────────────────────────────────────────────────────

    def leaderboard(self, *, limit: int = 50, month: int | None = None,
                    year: int | None = None, user_id: int | None = None) -> dict:
        """Monthly leaderboard (resets each calendar month) + the requesting
        user's row even when outside the top N."""
        now = _today()
        month = month or now.month
        year = year or now.year
        start = _month_start(date(year, month, 1))
        end = _next_month_start(date(year, month, 1))

        rows = (
            self.db.query(
                CoinLedger.user_id,
                sa_func.sum(CoinLedger.points).label('coins'),
            )
            .filter(CoinLedger.created_at >= start, CoinLedger.created_at < end)
            .group_by(CoinLedger.user_id)
            .order_by(sa_func.sum(CoinLedger.points).desc())
            .limit(limit)
            .all()
        )

        users = {u.id: u for u in self.db.query(User).filter(
            User.id.in_([r.user_id for r in rows] or [0])
        ).all()}

        entries = []
        for pos, (uid, coins) in enumerate(rows, start=1):
            u = users.get(uid)
            if not u:
                continue
            entries.append({
                'rank': pos,
                'user_id': uid,
                'name': u.full_name,
                'employee_code': u.employee_code,
                'department': u.department,
                'coins': int(coins or 0),
                'is_you': user_id is not None and uid == user_id,
            })

        # Requesting user's monthly row if not in the visible top N
        you = next((e for e in entries if e['is_you']), None)
        your_rank = None
        your_coins = 0
        if user_id is not None and not you:
            your_coins = self.month_coins(user_id, year=year, month=month)
            if your_coins > 0:
                better = (
                    self.db.query(sa_func.count(sa_func.distinct(CoinLedger.user_id)))
                    .filter(
                        CoinLedger.created_at >= start,
                        CoinLedger.created_at < end,
                    )
                    .group_by(CoinLedger.user_id)
                    .having(sa_func.sum(CoinLedger.points) > your_coins)
                    .scalar()
                )
                your_rank = (better or 0) + 1
                you = {
                    'rank': your_rank, 'user_id': user_id,
                    'name': None, 'employee_code': None, 'department': None,
                    'coins': your_coins, 'is_you': True,
                }
                # fill the name
                u = self.db.query(User).filter(User.id == user_id).first()
                if u:
                    you['name'] = u.full_name
                    you['employee_code'] = u.employee_code
                    you['department'] = u.department

        # "2.0k coins away from Top 5" style hint
        top5_threshold = entries[4]['coins'] if len(entries) >= 5 else None
        top5_gap = None
        if top5_threshold is not None and you and you['rank'] > 5:
            top5_gap = top5_threshold - your_coins + 1

        return {
            'month': month,
            'year': year,
            'month_name': date(year, month, 1).strftime('%B'),
            'entries': entries,
            'you': you,
            'top5_gap': top5_gap,
            'days_in_month': monthrange(year, month)[1],
        }

    # ─── Earning history ────────────────────────────────────────────────────

    def earning_history(self, user_id: int, *, limit: int = 30) -> list[dict]:
        rows = (
            self.db.query(CoinLedger)
            .filter(CoinLedger.user_id == user_id)
            .order_by(CoinLedger.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                'activity_type': r.activity_type,
                'coins': r.points,
                'module_id': r.module_id,
                'document_id': r.document_id,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    # ─── Hook API (called from other services) ──────────────────────────────

    def notify_event(self, user_id: int, event: str, **identifiers) -> int | None:
        """Convenience shim so other services can hook events without knowing
        the coin economy. Returns coins awarded or None."""
        mapping = {
            'chunk_completed': ('chunk_complete', {'reference_type': 'chunk', 'reference_id': 'chunk_id'}),
            'mcq_passed': ('mcq_pass', {'reference_type': 'mcq_attempt', 'reference_id': 'attempt_id'}),
            'mcq_failed': ('mcq_fail', {'reference_type': 'mcq_attempt', 'reference_id': 'attempt_id'}),
            'assignment_completed': ('assignment_complete', {'reference_type': 'assignment', 'reference_id': 'assignment_id'}),
            'daily_login': ('daily_login', {'reference_type': 'login', 'reference_id': 'date'}),
            'ppwec_interaction': ('ppwec_interaction', {'reference_type': 'screen', 'reference_id': 'screen_id'}),
            'ppwec_assessment': ('ppwec_assessment', {'reference_type': 'attempt', 'reference_id': 'attempt_id'}),
            'ppwec_challenge_day': ('ppwec_challenge_day', {'reference_type': 'challenge_day', 'reference_id': 'day_ref'}),
            'ppwec_challenge_bonus': ('ppwec_challenge_bonus', {'reference_type': 'challenge_bonus', 'reference_id': 'day_ref'}),
            'ppwec_module_completed': ('ppwec_module_complete', {'reference_type': 'module', 'reference_id': 'module_id'}),
        }
        if event not in mapping:
            logger.warning('Unknown gamification event: %s', event)
            return None

        activity, refspec = mapping[event]
        kwargs = {
            'user_id': user_id,
            'activity_type': activity,
            'reference_type': refspec['reference_type'],
        }
        # resolve reference id from identifiers
        ref_key = refspec['reference_id']
        if ref_key == 'date':
            kwargs['reference_id'] = _today().isoformat()
        else:
            kwargs['reference_id'] = identifiers.get(ref_key)

        kwargs['module_id'] = identifiers.get('module_id')
        kwargs['document_id'] = identifiers.get('document_id')
        kwargs['meta'] = identifiers.get('meta')
        return self.award(**kwargs)
