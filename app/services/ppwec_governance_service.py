"""PPWEC governance service — the §Y approval gate as working code.

Content validation (§X QA checklist), pilot feedback aggregation, reviewer
sign-offs per gate stage, and the Design Freeze toggle (§28).
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models.ppwec import PpwecModule, PpwecQuestion, PpwecScreen
from app.models.ppwec_governance import (
    PpwecDesignFreeze,
    PpwecPilotFeedback,
    PpwecReviewSignoff,
    PpwecValidationRun,
)
from app.models.user import User

logger = logging.getLogger(__name__)

GATE_STAGES = ['content_owner', 'md_leadership', 'ux', 'pilot']

INTERACTION_TYPES_REQUIRING_OPTIONS = {
    'choice', 'multi_choice', 'drag_sort', 'classification', 'simulation', 'branching',
}
KNOWN_INTERACTION_TYPES = INTERACTION_TYPES_REQUIRING_OPTIONS | {
    'content', 'click_reveal', 'reflection', 'assessment', 'challenge', 'commitment', 'completion',
}
KNOWN_SECTIONS = {
    'opening', 'learning', 'application', 'assessment', 'reflection', 'commitment', 'challenge', 'closure',
    'completion',  # §U final completion screen
}

# Payload keys that carry the scorable items per interaction family (§V: IT may
# adapt mechanics — the validator checks the family's real shape).
_OPTIONS_FAMILY = ('options', 'items', 'stages')
QUESTION_TYPES = {'knowledge', 'application', 'situational_judgement', 'case_based', 'decision'}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PpwecGovernanceService:
    def __init__(self, db: Session):
        self.db = db

    # ─── §X QA validation ───────────────────────────────────────────────────

    def validate_module(self, module_number: int) -> dict:
        """Run the §X QA checklist programmatically against authored content.

        Errors block sign-off; warnings are advisory (e.g. missing audio).
        """
        errors: list[dict] = []
        warnings: list[dict] = []

        m = self.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
        if not m:
            return {'ok': False, 'errors': [{'code': 'MODULE_MISSING', 'message': f'Module {module_number} not found'}],
                    'warnings': [], 'summary': {}}

        screens = (self.db.query(PpwecScreen)
                   .filter(PpwecScreen.module_id == m.id)
                   .order_by(PpwecScreen.screen_number).all())
        questions = (self.db.query(PpwecQuestion)
                     .filter(PpwecQuestion.module_id == m.id, PpwecQuestion.is_active.is_(True)).all())

        # Screen-count invariant: the master pack's screen economy (§V) targets
        # 30–35; below 10 the module is not viable.
        if len(screens) < 10:
            errors.append({'code': 'TOO_FEW_SCREENS', 'message': f'{len(screens)} screens authored; at least 10 required (target 30-35).'})

        seen_numbers = set()
        for s in screens:
            n = s.screen_number
            if n in seen_numbers:
                errors.append({'code': 'DUPLICATE_SCREEN_NUMBER', 'screen': n, 'message': f'Screen number {n} duplicated.'})
            seen_numbers.add(n)

            if not (s.title or '').strip():
                errors.append({'code': 'MISSING_TITLE', 'screen': n, 'message': 'Screen has no title.'})
            if s.section not in KNOWN_SECTIONS:
                errors.append({'code': 'BAD_SECTION', 'screen': n, 'message': f'Unknown section "{s.section}".'})
            if s.interaction_type not in KNOWN_INTERACTION_TYPES:
                errors.append({'code': 'BAD_INTERACTION_TYPE', 'screen': n, 'message': f'Unknown interaction_type "{s.interaction_type}".'})

            payload = s.interaction_payload or {}
            if s.interaction_type in INTERACTION_TYPES_REQUIRING_OPTIONS:
                # The scorable items live under the first family key present
                # (options for choices; items for drag/classification; stages
                # for sequential simulations — whose per-stage options nest).
                if s.interaction_type == 'simulation':
                    stages = payload.get('stages') or []
                    options = [o for st in stages for o in (st.get('options') or [])]
                    if not stages:
                        errors.append({'code': 'NO_SIMULATION_STAGES', 'screen': n,
                                       'message': 'Simulation has no stages.'})
                        continue
                else:
                    options = next((payload.get(k) or [] for k in _OPTIONS_FAMILY if payload.get(k)), [])
                if len(options) < 2:
                    errors.append({'code': 'TOO_FEW_OPTIONS', 'screen': n,
                                   'message': f'{s.interaction_type} needs >= 2 options; found {len(options)}.'})
                    continue
                # Correct-flag requirement applies only to option-family
                # payloads: drag/classification items are scorable by bucket
                # membership, simulation stages branch on `next` instead.
                if s.interaction_type in ('choice', 'multi_choice') and not any(o.get('correct') for o in options):
                    errors.append({'code': 'NO_CORRECT_FLAG', 'screen': n,
                                   'message': 'No option flagged correct — interaction is unscorable.'})
                for o in options:
                    label = o.get('text') or o.get('label') or o.get('scenario') or ''
                    if not str(label).strip():
                        errors.append({'code': 'EMPTY_OPTION_TEXT', 'screen': n, 'message': 'An option has empty text.'})

            if not (s.voice_over or '').strip():
                warnings.append({'code': 'NO_VOICE_OVER', 'screen': n,
                                 'message': 'No narration script (§A targets 60-70% coverage).'})
            elif not s.audio_url:
                warnings.append({'code': 'AUDIO_NOT_PRODUCED', 'screen': n,
                                 'message': 'Voice-over script exists but no audio asset attached.'})
            if s.audio_url and not s.caption_url:
                warnings.append({'code': 'NO_CAPTIONS', 'screen': n,
                                 'message': 'Audio attached without WebVTT captions (§23).'})
            if s.audio_url and not (s.transcript or '').strip():
                warnings.append({'code': 'NO_TRANSCRIPT', 'screen': n,
                                 'message': 'Audio attached without a11y transcript.'})

        # Assessment bank invariants (§15-§17)
        if len(questions) < 5:
            errors.append({'code': 'BANK_TOO_SMALL', 'message': f'{len(questions)} active questions; bank needs >= 5.'})
        if len(questions) < 10:
            warnings.append({'code': 'BANK_BELOW_TARGET', 'message': f'{len(questions)} questions; the doc targets a 10-15 bank for 10 served.'})
        for q in questions:
            opts = q.options or []
            keys = {o.get('key') for o in opts}
            if q.correct_option not in keys:
                errors.append({'code': 'BAD_CORRECT_KEY', 'question': q.id,
                               'message': f'correct_option "{q.correct_option}" not among option keys {sorted(k for k in keys if k)}.'})
            if not (q.feedback_why or '').strip() or not (q.feedback_better or '').strip():
                errors.append({'code': 'MISSING_RICH_FEEDBACK', 'question': q.id,
                               'message': 'Question missing §17 feedback_why / feedback_better.'})
            if q.question_type not in QUESTION_TYPES:
                warnings.append({'code': 'UNUSUAL_QUESTION_TYPE', 'question': q.id,
                                 'message': f'question_type "{q.question_type}" outside the documented set.'})

        # §11 cross-functional coverage: at least a few distinct functions implied
        # via visual direction / content; enforced softly.
        functions_mentioned = len({
            kw for s in screens
            for kw in ('quality', 'production', 'stores', 'engineering', 'hr')
            if kw in ((s.visual_direction or '') + ' ' + str(s.on_screen_text or '')).lower()
        })
        if functions_mentioned < 2:
            warnings.append({'code': 'THIN_CROSS_FUNCTIONAL', 'message': 'Fewer than 2 functions referenced — §11 wants multi-function examples.'})

        ok = not errors
        summary = {
            'screens': len(screens),
            'questions': len(questions),
            'errors': len(errors),
            'warnings': len(warnings),
            'passing_score': m.passing_score,
            'version': m.version,
            'status': m.status,
        }

        run = PpwecValidationRun(module_id=m.id, ok=ok, errors=errors, warnings=warnings)
        self.db.add(run)
        self.db.commit()

        return {'ok': ok, 'errors': errors, 'warnings': warnings, 'summary': summary}

    # ─── §Y pilot feedback ──────────────────────────────────────────────────

    def submit_pilot_feedback(self, module_number: int, user_id: int, *,
                              engagement: int, relevance: int, realism: int,
                              clarity: int, comments: str | None = None,
                              function_tag: str | None = None) -> dict:
        if not all(1 <= v <= 5 for v in (engagement, relevance, realism, clarity)):
            raise ValueError('Ratings must be 1..5')
        m = self.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
        if not m:
            raise ValueError(f'Module {module_number} not found')
        existing = self.db.query(PpwecPilotFeedback).filter(
            PpwecPilotFeedback.module_id == m.id, PpwecPilotFeedback.user_id == user_id).first()
        if existing:
            raise ValueError('Pilot feedback already submitted for this module')
        fb = PpwecPilotFeedback(
            module_id=m.id, user_id=user_id,
            engagement=engagement, relevance=relevance, realism=realism, clarity=clarity,
            overall_rating=round((engagement + relevance + realism + clarity) / 4, 2),
            comments=comments, function_tag=function_tag,
        )
        self.db.add(fb)
        self.db.commit()
        return {'id': fb.id, 'overall_rating': fb.overall_rating}

    def pilot_summary(self, module_number: int) -> dict:
        m = self.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
        if not m:
            raise ValueError(f'Module {module_number} not found')
        rows = self.db.query(PpwecPilotFeedback).filter(PpwecPilotFeedback.module_id == m.id).all()
        n = len(rows)
        def avg(attr):
            vals = [getattr(r, attr) for r in rows]
            return round(sum(vals) / n, 2) if n else None
        funcs = sorted({r.function_tag for r in rows if r.function_tag})
        return {
            'responses': n,
            'target_responses': 5,   # §Y: 5-10 cross-functional employees
            'avg': {
                'engagement': avg('engagement'),
                'relevance': avg('relevance'),
                'realism': avg('realism'),
                'clarity': avg('clarity'),
                'overall': avg('overall_rating'),
            },
            'functions_covered': funcs,
            'comments': [
                {'rating': r.overall_rating, 'comments': r.comments}
                for r in rows if r.comments
            ],
        }

    # ─── §Y sign-offs & freeze ──────────────────────────────────────────────

    def gate_status(self, module_number: int) -> dict:
        m = self.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
        if not m:
            raise ValueError(f'Module {module_number} not found')
        signoffs = (self.db.query(PpwecReviewSignoff)
                    .filter(PpwecReviewSignoff.module_id == m.id).all())
        by_stage = {}
        for so in signoffs:
            # keep the latest decision per (stage, reviewer); stage blocked if any changes_requested
            by_stage.setdefault(so.stage, []).append({
                'decision': so.decision, 'reviewer': so.reviewer_name,
                'decided_at': so.decided_at.isoformat() if so.decided_at else None,
                'notes': so.notes,
            })
        stage_states = {}
        for stage in GATE_STAGES:
            entries = by_stage.get(stage, [])
            if not entries:
                stage_states[stage] = 'pending'
            elif any(e['decision'] == 'changes_requested' for e in entries):
                stage_states[stage] = 'changes_requested'
            else:
                stage_states[stage] = 'approved'
        freeze = self.db.query(PpwecDesignFreeze).filter(
            PpwecDesignFreeze.module_id == m.id, PpwecDesignFreeze.unfrozen_at.is_(None)).first()
        pilot = self.pilot_summary(module_number)
        gate_open = all(v == 'approved' for v in stage_states.values()) and pilot['responses'] > 0
        return {
            'module_number': module_number,
            'module_status': m.status,
            'version': m.version,
            'content_owner': m.content_owner,
            'stages': stage_states,
            'signoffs': by_stage,
            'pilot': pilot,
            'design_frozen': bool(freeze),
            'frozen_at': freeze.frozen_at.isoformat() if freeze else None,
            'gate_open': gate_open,
        }

    def record_signoff(self, module_number: int, reviewer: User, stage: str, *,
                       decision: str, notes: str | None = None) -> dict:
        if stage not in GATE_STAGES:
            raise ValueError(f'stage must be one of {GATE_STAGES}')
        if decision not in ('approved', 'changes_requested'):
            raise ValueError('decision must be approved | changes_requested')
        m = self.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
        if not m:
            raise ValueError(f'Module {module_number} not found')
        if stage == 'pilot':
            n = self.db.query(PpwecPilotFeedback).filter(PpwecPilotFeedback.module_id == m.id).count()
            if n == 0:
                raise ValueError('Cannot sign off the pilot stage before pilot feedback exists')
        so = PpwecReviewSignoff(
            module_id=m.id, stage=stage, reviewer_id=reviewer.id,
            reviewer_name=reviewer.full_name, decision=decision, notes=notes,
        )
        self.db.add(so)
        # mirror the decision onto the module §29 status for catalog visibility
        if decision == 'approved':
            if stage == 'content_owner' and m.status == 'draft':
                m.status = 'reviewed'
            elif stage == 'md_leadership' and m.status == 'reviewed':
                m.status = 'approved'
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise ValueError('Sign-off already recorded for this reviewer at this stage')
        return {'stage': stage, 'decision': decision}

    def set_design_freeze(self, module_number: int, user: User, frozen: bool) -> dict:
        m = self.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
        if not m:
            raise ValueError(f'Module {module_number} not found')
        existing = self.db.query(PpwecDesignFreeze).filter(PpwecDesignFreeze.module_id == m.id).first()
        if frozen:
            # Gate must be green before freeze (§28), except Module 1's already-final status
            status = self.gate_status(module_number)
            if not status['gate_open'] and m.status != 'final':
                raise ValueError('Gate not green: all stages approved + pilot feedback required before freeze')
            if existing:
                existing.unfrozen_at = None
                existing.frozen_by_id = user.id
            else:
                screens = self.db.query(PpwecScreen).filter(PpwecScreen.module_id == m.id).count()
                existing = PpwecDesignFreeze(
                    module_id=m.id, frozen_by_id=user.id, frozen_by_name=user.full_name,
                    snapshot={'screens': screens, 'version': m.version,
                              'frozen_from_status': m.status},
                )
                self.db.add(existing)
            if m.status not in ('final',):
                m.status = 'final'
        else:
            if not existing:
                raise ValueError('Module is not frozen')
            existing.unfrozen_at = _utcnow()
            if m.status == 'final':
                m.status = 'approved'
        self.db.commit()
        return {'module_number': module_number, 'design_frozen': frozen,
                'frozen_at': existing.frozen_at.isoformat() if existing.frozen_at else None}
