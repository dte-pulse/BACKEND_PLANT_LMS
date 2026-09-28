"""PPWEC — Pulse Professional Workplace Excellence Certification service.

Implements the learner experience for the 18-module behavioural programme:
module player flow, interactive scoring, assessments (randomized, 80% pass,
retry), the Pulse Learning Passport(TM), gamification points, badges, the
7-Day Challenge and certificate/report data.

Content governance (S25): learning intent lives in the authored content
(screens/questions); this service orchestrates delivery and scoring only.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models.ppwec import (
    PpwecAssessmentAttempt,
    PpwecBadge,
    PpwecChallengeProgress,
    PpwecModule,
    PpwecPointsLedger,
    PpwecQuestion,
    PpwecScreen,
    PpwecUserBadge,
    PpwecUserModuleState,
)
from app.models.user import User

logger = logging.getLogger(__name__)


def _coins(db, user_id: int, event: str, **identifiers) -> int | None:
    """Best-effort gamification hook: never break the learning flow."""
    try:
        from app.services.gamification_service import GamificationService
        return GamificationService(db).notify_event(user_id, event, **identifiers)
    except Exception:  # noqa: BLE001 — rewards must never break learning
        logger.debug('Gamification hook failed for %s', event, exc_info=True)
        return None


def _utcnow() -> datetime:
    """TZ-aware now, normalized to naive for SQLite (naive column storage).

    Postgres columns are timestamptz, SQLite stores naive datetimes; comparing
    a tz-aware `now` against a naive SQLite value raises TypeError. Normalizing
    to naive UTC works consistently on both engines.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt: datetime | None) -> datetime | None:
    """Coerce a stored datetime to naive UTC for safe comparisons.

    Postgres (timestamptz) returns aware values, SQLite returns naive ones;
    normalizing both sides avoids offset-naive/aware TypeError on either engine.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt

# Gamification scoring per SS V.3 of the master content pack. IT may normalize
# these via env override later; kept as constants for the prototype.
POINTS = {
    'interaction': 10,          # interactive activities (5-10)
    'scenario_decision': 10,    # scenario decisions
    'simulation': 25,           # major simulation
    'assessment': 100,          # module assessment
    'challenge': 70,            # 7-Day Challenge (max)
    'challenge_day': 10,        # per completed challenge day
    'module_completion': 25,    # module completion
}

CHALLENGE_DAYS = 7
CHALLENGE_WINDOW_DAYS = 10  # "next seven working days" ~ 10 calendar days

FINAL_BADGE_NAME = 'Pulse Professional Excellence Champion'

# Display letters for randomized answer order (§16).
LETTERS = 'ABCDEFGH'


class PpwecService:
    def __init__(self, db: Session):
        self.db = db

    # ─── Catalog ─────────────────────────────────────────────────────────────

    def list_modules(self) -> list[dict]:
        modules = self.db.query(PpwecModule).order_by(PpwecModule.module_number).all()
        return [self._module_dict(m) for m in modules]

    def get_module_detail(self, module_number: int) -> dict | None:
        m = self._get_module(module_number)
        if not m:
            return None
        screens = self._screens(m.id)
        d = self._module_dict(m)
        d['screens'] = [self._screen_dict(s, include_payload=True) for s in screens]
        d['screen_count'] = len(screens)
        return d

    def _get_module(self, module_number: int) -> PpwecModule | None:
        return (
            self.db.query(PpwecModule)
            .filter(PpwecModule.module_number == module_number)
            .first()
        )

    def _screens(self, module_id: int) -> list[PpwecScreen]:
        return (
            self.db.query(PpwecScreen)
            .filter(PpwecScreen.module_id == module_id)
            .order_by(PpwecScreen.screen_number)
            .all()
        )

    # ─── Player flow ─────────────────────────────────────────────────────────

    def start_module(self, user_id: int, module_number: int) -> dict:
        """Return the navigation state for resuming a module (S23 resume)."""
        m = self._require_module(module_number)
        state = self._get_or_create_state(user_id, m.id)

        screens = self._screens(m.id)
        completed = set(state.screens_completed or [])
        mandatory_ids = {s.id for s in screens if s.is_mandatory}

        # Resume point: first mandatory screen not yet completed, else last screen.
        resume = next((s for s in screens if s.is_mandatory and s.id not in completed), None)
        if resume is None:
            resume = screens[-1] if screens else None

        # Unlock map: screen i unlocked if all previous mandatory screens done.
        unlocked_ids: set[int] = set()
        for s in screens:
            unlocked_ids.add(s.id)
            if s.is_mandatory and s.id not in completed:
                break

        return {
            'module': self._module_dict(m),
            'current_screen_id': state.current_screen_id or (resume.id if resume else None),
            'resume_screen_id': resume.id if resume else None,
            'completed_screen_ids': sorted(completed),
            'unlocked_screen_ids': sorted(unlocked_ids),
            'mandatory_screen_ids': sorted(mandatory_ids),
            'completion_percentage': state.completion_percentage,
            'status': state.status,
            'best_score': state.best_score,
            'passed': state.passed,
        }

    def complete_screen(
        self,
        user_id: int,
        module_number: int,
        screen_id: int,
        interaction_result: dict | None = None,
        time_spent_seconds: int = 0,
    ) -> dict:
        """Record a screen completion, score the interaction, award points.

        No-skip (S V.1): mandatory interactive screens gate progression.
        """
        m = self._require_module(module_number)
        screen = self.db.query(PpwecScreen).filter(
            PpwecScreen.id == screen_id, PpwecScreen.module_id == m.id
        ).first()
        if not screen:
            raise ValueError('Screen not found in this module')

        state = self._get_or_create_state(user_id, m.id)
        if state.status == 'not_started':
            state.status = 'in_progress'
            state.started_at = _utcnow()

        completed = set(state.screens_completed or [])
        first_completion = screen.id not in completed
        points_delta = 0

        # Score the interaction & award points only on first completion.
        feedback = None
        if first_completion and screen.interaction_type in (
            'choice', 'multi_choice', 'drag_sort', 'classification', 'simulation', 'branching'
        ):
            feedback = self._score_interaction(screen, interaction_result or {})
            if feedback.get('correct'):
                points_delta += self._award_points(
                    user_id,
                    'simulation' if screen.interaction_type in ('simulation', 'branching') else 'interaction',
                    m.id,
                    reference_id=str(screen.id),
                )

        completed.add(screen.id)
        state.screens_completed = sorted(completed)
        state.current_screen_id = screen.id
        state.time_spent_seconds = (state.time_spent_seconds or 0) + max(0, int(time_spent_seconds))

        total = self.db.query(PpwecScreen).filter(PpwecScreen.module_id == m.id).count()
        state.completion_percentage = round(100.0 * len(completed) / total, 1) if total else 0.0

        # All mandatory screens done -> assessment unlocks.
        mandatory_ids = {s.id for s in self._screens(m.id) if s.is_mandatory}
        if mandatory_ids and mandatory_ids.issubset(completed) and state.status == 'in_progress':
            state.status = 'assessment_pending'

        # Gamification: reward the interaction itself (first completion only).
        coins_delta = 0
        if first_completion:
            coins_delta = _coins(self.db, user_id, 'ppwec_interaction', screen_id=screen.id, module_id=m.id) or 0

        self.db.commit()

        unlocked = self._unlock_map(m.id, completed)
        return {
            'screen_id': screen.id,
            'completion_percentage': state.completion_percentage,
            'status': state.status,
            'points_delta': points_delta,
            'coins_delta': coins_delta,
            'feedback': feedback,
            'unlocked_screen_ids': sorted(unlocked),
            'assessment_unlocked': state.status in ('assessment_pending', 'completed'),
        }

    def _score_interaction(self, screen: PpwecScreen, result: dict) -> dict:
        """Score an interaction against its payload. Returns rich feedback.

        Payload contract (authored content):
          options: [{key, text, correct: bool, feedback: str, consequence: str?}]
        Branching simulations may define per-option `next` without `correct`.
        """
        payload = screen.interaction_payload or {}
        options = payload.get('options', [])
        selected = result.get('selected')

        by_key = {o.get('key'): o for o in options}
        opt = by_key.get(selected)
        if opt is None:
            return {'correct': False, 'title': 'No selection', 'message': 'Please choose an option to continue.'}

        correct = bool(opt.get('correct'))
        return {
            'correct': correct,
            'title': 'Correct' if correct else 'Not quite',
            'message': opt.get('feedback') or payload.get('generic_feedback') or '',
            'consequence': opt.get('consequence'),  # branching: show what happens next (S14)
            'better_approach': next(
                (o.get('text') for o in options if o.get('correct')), None
            ),
        }

    def _unlock_map(self, module_id: int, completed: set[int]) -> set[int]:
        screens = self._screens(module_id)
        unlocked: set[int] = set()
        for s in screens:
            unlocked.add(s.id)
            if s.is_mandatory and s.id not in completed:
                break
        return unlocked

    # ─── Assessment (S15-17) ─────────────────────────────────────────────────

    def start_assessment(self, user_id: int, module_number: int) -> dict:
        """Serve a randomized question set. Persists the served snapshot."""
        m = self._require_module(module_number)
        self._ensure_state_active(user_id, m)

        questions = (
            self.db.query(PpwecQuestion)
            .filter(PpwecQuestion.module_id == m.id, PpwecQuestion.is_active.is_(True))
            .all()
        )
        if len(questions) < 5:
            raise ValueError('Assessment bank needs at least 5 active questions')

        import random
        random.shuffle(questions)
        served = questions[:10] if len(questions) >= 10 else questions

        # §16 answer-order randomization: shuffle each question's options and
        # record the ORIGINAL keys in DISPLAY order. The learner sees fresh
        # letters (A, B, C, ...) per attempt; grading maps display keys back to
        # the stored correct keys via this snapshot.
        letters = 'ABCDEFGH'
        option_orders: dict[int, list[str]] = {}
        for q in served:
            keys = [o.get('key') for o in (q.options or []) if o.get('key') is not None]
            random.shuffle(keys)
            option_orders[q.id] = keys

        attempt_number = (
            self.db.query(sa_func.max(PpwecAssessmentAttempt.attempt_number))
            .filter(
                PpwecAssessmentAttempt.user_id == user_id,
                PpwecAssessmentAttempt.module_id == m.id,
            )
            .scalar()
        ) or 0

        attempt = PpwecAssessmentAttempt(
            user_id=user_id,
            module_id=m.id,
            question_ids=[q.id for q in served],
            option_orders=option_orders,
            attempt_number=attempt_number + 1,
        )
        self.db.add(attempt)
        self.db.commit()

        return {
            'attempt_id': attempt.id,
            'attempt_number': attempt.attempt_number,
            'passing_score': m.passing_score,
            'questions': [self._question_dict(q, include_answer=False, option_order=option_orders[q.id]) for q in served],
        }

    def submit_assessment(
        self, user_id: int, module_number: int, attempt_id: int, answers: dict
    ) -> dict:
        """Grade an attempt, apply the 80% rule, award points/badge on pass."""
        m = self._require_module(module_number)
        attempt = self.db.query(PpwecAssessmentAttempt).filter(
            PpwecAssessmentAttempt.id == attempt_id,
            PpwecAssessmentAttempt.user_id == user_id,
            PpwecAssessmentAttempt.module_id == m.id,
        ).first()
        if not attempt:
            raise ValueError('Assessment attempt not found')
        if attempt.submitted_at is not None:
            raise ValueError('Attempt already submitted')

        served = (
            self.db.query(PpwecQuestion)
            .filter(PpwecQuestion.id.in_(attempt.question_ids))
            .all()
        )
        by_id = {q.id: q for q in served}
        option_orders = attempt.option_orders or {}

        results = []
        correct_count = 0
        for qid in attempt.question_ids:
            q = by_id.get(qid)
            if not q:
                continue
            selected = (answers or {}).get(str(qid)) or (answers or {}).get(qid)
            # Map the displayed letter back to the ORIGINAL option key (§16).
            display_keys = option_orders.get(str(qid)) or option_orders.get(qid)
            if display_keys:
                try:
                    display_idx = LETTERS.index(str(selected).upper())
                    selected_original = display_keys[display_idx] if 0 <= display_idx < len(display_keys) else selected
                except ValueError:
                    selected_original = selected
            else:
                selected_original = selected
            is_correct = selected_original is not None and str(selected_original).upper() == str(q.correct_option).upper()
            if is_correct:
                correct_count += 1
            results.append({
                'question_id': qid,
                'selected': selected,
                'correct': is_correct,
                'correct_option': q.correct_option,
                'feedback_why': q.feedback_why,        # S17 rich feedback
                'feedback_better': q.feedback_better,
            })

        score = round(100.0 * correct_count / len(results), 1) if results else 0.0
        passed = score >= m.passing_score

        attempt.answers = answers or {}
        attempt.score = score
        attempt.passed = passed
        attempt.submitted_at = _utcnow()

        state = self._get_or_create_state(user_id, m.id)
        points_delta = 0
        coins_earned = 0
        if passed:
            state.passed = True
            state.best_score = max(state.best_score or 0.0, score)
            state.status = 'completed'
            state.completed_at = _utcnow()
            points_delta += self._award_points(user_id, 'assessment', m.id, reference_id=str(attempt.id))
            points_delta += self._award_points(user_id, 'module_completion', m.id)
            badge = self._award_module_badge(user_id, m)
            self._check_final_badge(user_id)
            # Gamification: assessment pass + module completion coins.
            coins_earned += _coins(self.db, user_id, 'ppwec_assessment', attempt_id=str(attempt.id), module_id=m.id) or 0
            coins_earned += _coins(self.db, user_id, 'ppwec_module_completed', module_id=str(m.id)) or 0
        elif (state.best_score or 0) < score:
            state.best_score = score  # track best across retries; retry stays open

        self.db.commit()

        return {
            'attempt_id': attempt.id,
            'score': score,
            'passed': passed,
            'passing_score': m.passing_score,
            'correct_count': correct_count,
            'total': len(results),
            'retry_allowed': True,  # S16: objective is learning, not failure
            'points_delta': points_delta,
            'coins_earned': coins_earned,
            'badge_awarded': m.badge_name if passed else None,
            'results': results,
        }

    # ─── Learning Passport (S19) ─────────────────────────────────────────────

    def get_passport(self, user_id: int) -> dict:
        modules = self.db.query(PpwecModule).order_by(PpwecModule.module_number).all()
        states = {
            s.module_id: s
            for s in self.db.query(PpwecUserModuleState)
            .filter(PpwecUserModuleState.user_id == user_id)
            .all()
        }
        total_points = (
            self.db.query(sa_func.coalesce(sa_func.sum(PpwecPointsLedger.points), 0))
            .filter(PpwecPointsLedger.user_id == user_id)
            .scalar()
        )
        badges = (
            self.db.query(PpwecUserBadge, PpwecBadge)
            .join(PpwecBadge, PpwecUserBadge.badge_id == PpwecBadge.id)
            .filter(PpwecUserBadge.user_id == user_id)
            .all()
        )
        completed = sum(1 for m in modules if states.get(m.id) and states[m.id].status == 'completed')

        return {
            'total_modules': len(modules),
            'modules_completed': completed,
            'total_points': int(total_points or 0),
            'certification_earned': bool(modules) and completed == len(modules),
            'modules': [
                {
                    **self._module_dict(m),
                    'progress': self._state_dict(states.get(m.id)),
                }
                for m in modules
            ],
            'badges': [
                {
                    'name': b.name,
                    'description': b.description,
                    'icon': b.icon,
                    'is_final': b.is_final,
                    'awarded_at': ub.awarded_at.isoformat() if ub.awarded_at else None,
                }
                for ub, b in badges
            ],
        }

    def my_overview(self, user_id: int) -> dict:
        """Compact overview for dashboard cards."""
        passport = self.get_passport(user_id)
        in_progress = [
            m for m in passport['modules']
            if m['progress']['status'] in ('in_progress', 'assessment_pending')
        ]
        not_started = [
            m for m in passport['modules'] if m['progress']['status'] == 'not_started'
        ]
        next_module = (
            in_progress[0] if in_progress
            else (not_started[0] if not_started else None)
        )
        return {
            'modules_completed': passport['modules_completed'],
            'total_modules': passport['total_modules'],
            'total_points': passport['total_points'],
            'badge_count': len(passport['badges']),
            'certification_earned': passport['certification_earned'],
            'next_module': next_module,
        }

    # ─── Points & badges ─────────────────────────────────────────────────────

    def _award_points(self, user_id: int, activity_type: str, module_id: int | None,
                      reference_id: str | None = None, points: int | None = None) -> int:
        pts = points if points is not None else POINTS.get(activity_type, 0)
        self.db.add(PpwecPointsLedger(
            user_id=user_id,
            activity_type=activity_type,
            points=pts,
            module_id=module_id,
            reference_id=reference_id,
        ))
        state = self._get_or_create_state(user_id, module_id) if module_id else None
        if state:
            state.points_earned = (state.points_earned or 0) + pts
        return pts

    def _award_module_badge(self, user_id: int, module: PpwecModule) -> str | None:
        if not module.badge_name or not state_passed(module, user_id, self.db):
            return None
        badge = self.db.query(PpwecBadge).filter(PpwecBadge.name == module.badge_name).first()
        if not badge:
            badge = PpwecBadge(
                name=module.badge_name,
                description=f'Micro-badge for completing Module {module.module_number}: {module.title}',
                icon=module.badge_icon or 'medal',
                is_final=False,
                required_modules=1,
            )
            self.db.add(badge)
            self.db.flush()
        existing = self.db.query(PpwecUserBadge).filter(
            PpwecUserBadge.user_id == user_id,
            PpwecUserBadge.badge_id == badge.id,
        ).first()
        if not existing:
            self.db.add(PpwecUserBadge(user_id=user_id, badge_id=badge.id, module_id=module.id))
            self._notify(user_id, 'Badge Earned', f"You earned the '{badge.name}' badge!", 'success')
        return badge.name

    def _check_final_badge(self, user_id: int) -> None:
        total_modules = self.db.query(PpwecModule).count()
        completed = (
            self.db.query(PpwecUserModuleState)
            .filter(
                PpwecUserModuleState.user_id == user_id,
                PpwecUserModuleState.status == 'completed',
            )
            .count()
        )
        if total_modules == 0 or completed < total_modules:
            return
        badge = self.db.query(PpwecBadge).filter(PpwecBadge.is_final.is_(True)).first()
        if not badge:
            badge = PpwecBadge(
                name=FINAL_BADGE_NAME,
                description='Completed all 18 PPWEC modules — Pulse Professional Excellence Champion.',
                icon='trophy',
                is_final=True,
                required_modules=total_modules,
            )
            self.db.add(badge)
            self.db.flush()
        existing = self.db.query(PpwecUserBadge).filter(
            PpwecUserBadge.user_id == user_id,
            PpwecUserBadge.badge_id == badge.id,
        ).first()
        if not existing:
            self.db.add(PpwecUserBadge(user_id=user_id, badge_id=badge.id))
            self._notify(user_id, 'Certification Achieved',
                         'Congratulations! You are a Pulse Professional Excellence Champion.', 'success')

    def _notify(self, user_id: int, title: str, message: str, ntype: str = 'info') -> None:
        try:
            from app.services.notification_service import NotificationService
            NotificationService(self.db).publish(user_id, title, message, ntype)
        except Exception:  # noqa: BLE001 — notifications must never break scoring
            logger.debug('PPWEC notification failed for user %s', user_id, exc_info=True)

    # ─── 7-Day Challenge (S22, SQ) ───────────────────────────────────────────

    def start_challenge(self, user_id: int, module_number: int) -> dict:
        m = self._require_module(module_number)
        existing = self.db.query(PpwecChallengeProgress).filter(
            PpwecChallengeProgress.user_id == user_id,
            PpwecChallengeProgress.module_id == m.id,
        ).first()
        if existing:
            return self._challenge_dict(existing)
        cp = PpwecChallengeProgress(
            user_id=user_id,
            module_id=m.id,
            days_completed=[],
            expires_at=_utcnow() + timedelta(days=CHALLENGE_WINDOW_DAYS),
        )
        self.db.add(cp)
        self.db.commit()
        return self._challenge_dict(cp)

    def tick_challenge_day(self, user_id: int, module_number: int, day: int, done: bool) -> dict:
        """Check off one challenge day; awards 10 pts/day up to 70 (SQ)."""
        m = self._require_module(module_number)
        cp = self.db.query(PpwecChallengeProgress).filter(
            PpwecChallengeProgress.user_id == user_id,
            PpwecChallengeProgress.module_id == m.id,
        ).first()
        if not cp:
            raise ValueError('Challenge not started for this module')
        if cp.completed_at:
            return self._challenge_dict(cp)
        if cp.expires_at and _utcnow() > _naive(cp.expires_at):
            raise ValueError('Challenge window has expired')

        days = set(cp.days_completed or [])
        newly = None
        if done and day not in days and 1 <= day <= CHALLENGE_DAYS:
            days.add(day)
            newly = day
            self._award_points(user_id, 'challenge_day', m.id,
                               reference_id=f'challenge-day-{day}', points=POINTS['challenge_day'])
            _coins(self.db, user_id, 'ppwec_challenge_day', day_ref=f'{m.id}-{day}', module_id=m.id)
        elif not done:
            days.discard(day)
        cp.days_completed = sorted(days)

        if len(cp.days_completed) == CHALLENGE_DAYS:
            cp.completed_at = _utcnow()
            self._award_points(user_id, 'challenge', m.id)  # completion bonus -> ledger total
            _coins(self.db, user_id, 'ppwec_challenge_bonus', day_ref=f'{m.id}-bonus', module_id=m.id)
            self._notify(user_id, 'Challenge Complete',
                         f'7-Day Challenge for "{m.title}" completed. Well done!', 'success')

        self.db.commit()
        d = self._challenge_dict(cp)
        d['newly_completed_day'] = newly
        return d

    # ─── Reports (S24) ───────────────────────────────────────────────────────

    def org_report(self) -> dict:
        """Organization-level PPWEC compliance: started/in-progress/completed."""
        total_users = self.db.query(User).filter(User.is_active.is_(True)).count()
        total_modules = self.db.query(PpwecModule).count()

        states = self.db.query(PpwecUserModuleState).all()
        by_module: dict[int, list[PpwecUserModuleState]] = {}
        for s in states:
            by_module.setdefault(s.module_id, []).append(s)

        modules = self.db.query(PpwecModule).order_by(PpwecModule.module_number).all()
        module_rows = []
        for m in modules:
            sts = by_module.get(m.id, [])
            completed = [s for s in sts if s.status == 'completed']
            in_prog = [s for s in sts if s.status in ('in_progress', 'assessment_pending')]
            scores = [s.best_score for s in completed if s.best_score is not None]
            module_rows.append({
                'module_number': m.module_number,
                'title': m.title,
                'status': m.status,
                'not_started': max(0, total_users - len(sts)),
                'in_progress': len(in_prog),
                'completed': len(completed),
                'avg_score': round(sum(scores) / len(scores), 1) if scores else None,
            })

        all_scores = [s.best_score for s in states if s.status == 'completed' and s.best_score is not None]
        completed_any = len({s.user_id for s in states if s.status == 'completed'})
        started_any = len({s.user_id for s in states if s.status != 'not_started'})

        return {
            'total_employees': total_users,
            'total_modules': total_modules,
            'started': started_any,
            'in_progress': len({s.user_id for s in states if s.status in ('in_progress', 'assessment_pending')}),
            'completed': completed_any,
            'not_started': max(0, total_users - started_any),
            'avg_score': round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
            'certified': self._certified_user_count(),
            'modules': module_rows,
        }

    def department_report(self) -> list[dict]:
        """Function-level view (S24): department rows with module progress."""
        users = self.db.query(User).filter(User.is_active.is_(True)).all()
        dept_of = {u.id: (u.department or 'Unassigned') for u in users}

        states = self.db.query(PpwecUserModuleState).all()
        total_modules = self.db.query(PpwecModule).count() or 1

        rows: dict[str, dict] = {}
        for u in users:
            d = dept_of[u.id]
            r = rows.setdefault(d, {
                'department': d, 'employees': 0, 'started': 0,
                'completed': 0, 'certified': 0, 'score_sum': 0.0, 'score_n': 0,
            })
            r['employees'] += 1

        for s in states:
            d = dept_of.get(s.user_id)
            if not d:
                continue
            r = rows[d]
            if s.status != 'not_started':
                r['started'] += 1
            if s.status == 'completed':
                r['completed'] += 1
                if s.best_score is not None:
                    r['score_sum'] += s.best_score
                    r['score_n'] += 1

        certified_ids = self._certified_user_ids()
        for u in users:
            if u.id in certified_ids:
                rows[dept_of[u.id]]['certified'] += 1

        out = []
        for r in rows.values():
            out.append({
                'department': r['department'],
                'employees': r['employees'],
                'started': r['started'],
                'completed': r['completed'],
                'completion_rate': round(100.0 * r['completed'] / (r['employees'] * total_modules), 1) if r['employees'] else 0.0,
                'avg_score': round(r['score_sum'] / r['score_n'], 1) if r['score_n'] else None,
                'certified': r['certified'],
            })
        out.sort(key=lambda x: x['department'])
        return out

    def certificate_data(self, user_id: int) -> dict:
        """Data backing the final certificate (S20, S23)."""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError('User not found')
        passport = self.get_passport(user_id)
        return {
            'employee_name': user.full_name,
            'employee_code': user.employee_code,
            'department': user.department,
            'modules_completed': passport['modules_completed'],
            'total_modules': passport['total_modules'],
            'total_points': passport['total_points'],
            'certification_earned': passport['certification_earned'],
            'certification_title': 'Pulse Professional Workplace Excellence Certification',
            'credential': FINAL_BADGE_NAME,
            'issued_at': datetime.now(timezone.utc).isoformat(),
        }

    def csv_report(self) -> str:
        """Individual-level CSV for HR export (S24)."""
        states = {
            (s.user_id, s.module_id): s
            for s in self.db.query(PpwecUserModuleState).all()
        }
        modules = self.db.query(PpwecModule).order_by(PpwecModule.module_number).all()
        users = self.db.query(User).filter(User.is_active.is_(True)).all()

        lines = ['employee_code,employee_name,department,'
                 + ','.join(f'M{m.module_number:02d}_status,M{m.module_number:02d}_score' for m in modules)
                 + ',modules_completed,total_points,certified']
        for u in users:
            cells = []
            done = 0
            total_points = 0
            for m in modules:
                s = states.get((u.id, m.id))
                status = s.status if s else 'not_started'
                score = '' if not s or s.best_score is None else f'{s.best_score:g}'
                cells.append(f'{status},{score}')
                if status == 'completed':
                    done += 1
                total_points += s.points_earned if s else 0
            certified = 'yes' if self._is_certified(u.id) else 'no'
            name = (u.full_name or '').replace(',', ' ')
            lines.append(
                f'{u.employee_code},{name},{u.department or ""},'
                + ','.join(cells) + f',{done},{total_points},{certified}'
            )
        return '\n'.join(lines)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _require_module(self, module_number: int) -> PpwecModule:
        m = self._get_module(module_number)
        if not m:
            raise ValueError(f'PPWEC module {module_number} not found')
        return m

    def _ensure_state_active(self, user_id: int, module: PpwecModule) -> None:
        state = self._get_or_create_state(user_id, module.id)
        mandatory = {s.id for s in self._screens(module.id) if s.is_mandatory}
        completed = set(state.screens_completed or [])
        if mandatory and not mandatory.issubset(completed):
            raise ValueError('Complete all mandatory learning screens before the assessment')

    def _get_or_create_state(self, user_id: int, module_id: int) -> PpwecUserModuleState:
        state = (
            self.db.query(PpwecUserModuleState)
            .filter(
                PpwecUserModuleState.user_id == user_id,
                PpwecUserModuleState.module_id == module_id,
            )
            .first()
        )
        if not state:
            state = PpwecUserModuleState(user_id=user_id, module_id=module_id, screens_completed=[])
            self.db.add(state)
            self.db.flush()
        return state

    def _certified_user_ids(self) -> set[int]:
        total_modules = self.db.query(PpwecModule).count()
        if total_modules == 0:
            return set()
        rows = (
            self.db.query(PpwecUserModuleState.user_id,
                          sa_func.count(PpwecUserModuleState.module_id))
            .filter(PpwecUserModuleState.status == 'completed')
            .group_by(PpwecUserModuleState.user_id)
            .all()
        )
        return {uid for uid, cnt in rows if cnt >= total_modules}

    def _certified_user_count(self) -> int:
        return len(self._certified_user_ids())

    def _is_certified(self, user_id: int) -> bool:
        return user_id in self._certified_user_ids()

    def _module_dict(self, m: PpwecModule) -> dict:
        return {
            'id': m.id,
            'module_number': m.module_number,
            'title': m.title,
            'theme': m.theme,
            'description': m.description,
            'estimated_minutes': m.estimated_minutes,
            'passing_score': m.passing_score,
            'badge_name': m.badge_name,
            'badge_icon': m.badge_icon,
            'status': m.status,
            'version': m.version,
            'content_owner': m.content_owner,
            'is_mandatory': m.is_mandatory,
        }

    def _screen_dict(self, s: PpwecScreen, include_payload: bool = False) -> dict:
        d = {
            'id': s.id,
            'screen_number': s.screen_number,
            'section': s.section,
            'title': s.title,
            'interaction_type': s.interaction_type,
            'is_mandatory': s.is_mandatory,
            'estimated_seconds': s.estimated_seconds,
            'pulse_anchor': s.pulse_anchor,
            'md_philosophy': s.md_philosophy,
            'has_audio': bool(s.audio_url),
            'has_video': bool(s.video_url),
        }
        if include_payload:
            d.update({
                'on_screen_text': s.on_screen_text,
                'voice_over': s.voice_over,
                'audio_url': s.audio_url,
                'caption_url': s.caption_url,
                'transcript': s.transcript,
                'video_url': s.video_url,
                'visual_direction': s.visual_direction,
                'interaction_payload': s.interaction_payload,
            })
        return d

    def _question_dict(self, q: PpwecQuestion, include_answer: bool = False,
                       option_order: list[str] | None = None) -> dict:
        d = {
            'id': q.id,
            'question_type': q.question_type,
            'case_context': q.case_context,
            'question_text': q.question_text,
            'options': self._display_options(q, option_order),
        }
        if include_answer:
            d.update({
                'correct_option': q.correct_option,
                'feedback_why': q.feedback_why,
                'feedback_better': q.feedback_better,
            })
        return d

    @staticmethod
    def _display_options(q: PpwecQuestion, option_order: list[str] | None) -> list[dict]:
        """Serve options in the attempt's randomized order with fresh display
        letters (A, B, C, ...). Falls back to stored order when no snapshot
        exists (legacy attempts / authoring previews)."""
        options = list(q.options or [])
        if not option_order:
            return options
        by_key = {o.get('key'): o for o in options}
        served = []
        for pos, key in enumerate(option_order):
            base = by_key.get(key)
            if base is None:
                continue
            served.append({**base, 'key': LETTERS[pos] if pos < len(LETTERS) else str(pos + 1)})
        return served or options

    def _state_dict(self, s: PpwecUserModuleState | None) -> dict:
        if not s:
            return {
                'status': 'not_started', 'completion_percentage': 0.0,
                'best_score': None, 'passed': False, 'badge_awarded': False,
                'points_earned': 0, 'completed_at': None,
            }
        return {
            'status': s.status,
            'completion_percentage': s.completion_percentage,
            'best_score': s.best_score,
            'passed': s.passed,
            'badge_awarded': s.badge_awarded,
            'points_earned': s.points_earned,
            'completed_at': s.completed_at.isoformat() if s.completed_at else None,
        }

    def _challenge_dict(self, cp: PpwecChallengeProgress) -> dict:
        return {
            'module_id': cp.module_id,
            'days_completed': cp.days_completed or [],
            'points_awarded': cp.points_awarded,
            'expires_at': cp.expires_at.isoformat() if cp.expires_at else None,
            'completed_at': cp.completed_at.isoformat() if cp.completed_at else None,
        }


def state_passed(module: PpwecModule, user_id: int, db: Session) -> bool:
    """Helper used by badge awarding to confirm a real pass exists."""
    s = (
        db.query(PpwecUserModuleState)
        .filter(
            PpwecUserModuleState.user_id == user_id,
            PpwecUserModuleState.module_id == module.id,
        )
        .first()
    )
    return bool(s and s.passed)
