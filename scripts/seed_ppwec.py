"""Seed the PPWEC programme content.

- Creates the 18-module catalog (titles + themes per §5 of the master pack).
- Seeds Module 1 (Professionalism at the Workplace) fully: interactive
  screens + the 10-question assessment bank with §17 rich feedback.
- Idempotent: safe to re-run (upserts by module_number / screen_number).

Run:  cd Backend && python -m scripts.seed_ppwec
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.ppwec import (  # noqa: E402
    PpwecModule,
    PpwecQuestion,
    PpwecScreen,
)
from scripts.ppwec_content_m1 import CATALOG, MODULE_1_BADGE, QUESTIONS, SCREENS  # noqa: E402

CONTENT_OWNER = 'Praveen Tiwari — Head Field HR'  # §26
MODULE_STATUSES = {1: 'final'}  # Module 1 = approved prototype, live for learners (§28–29)


def seed() -> None:
    db = SessionLocal()
    try:
        # ── 18-module catalog ────────────────────────────────────────────────
        for number, title, theme in CATALOG:
            m = db.query(PpwecModule).filter(PpwecModule.module_number == number).first()
            if not m:
                m = PpwecModule(module_number=number, title=title, theme=theme)
                db.add(m)
            m.title = title
            m.theme = theme
            m.status = MODULE_STATUSES.get(number, 'draft')
            m.version = f'PPWEC-M{number:02d}-V1.0'
            m.content_owner = CONTENT_OWNER
            m.passing_score = 80.0
            m.estimated_minutes = 60
            if number == 1:
                m.badge_name = MODULE_1_BADGE
                m.badge_icon = 'medal'
            db.flush()

        m1 = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()

        # ── Module 1 screens ─────────────────────────────────────────────────
        for spec in SCREENS:
            s = db.query(PpwecScreen).filter(
                PpwecScreen.module_id == m1.id,
                PpwecScreen.screen_number == spec['screen_number'],
            ).first()
            if not s:
                s = PpwecScreen(module_id=m1.id, screen_number=spec['screen_number'])
                db.add(s)
            for field in ('section', 'title', 'on_screen_text', 'voice_over', 'audio_url',
                          'caption_url', 'transcript', 'video_url', 'visual_direction',
                          'interaction_type', 'interaction_payload', 'is_mandatory',
                          'estimated_seconds', 'pulse_anchor', 'md_philosophy'):
                setattr(s, field, spec.get(field, None if field == 'interaction_payload' else False)
                        if field not in ('section', 'title', 'interaction_type', 'estimated_seconds', 'is_mandatory')
                        else spec.get(field))
            # deterministic defaults for non-nullable fields
            s.section = spec.get('section', 'learning')
            s.title = spec['title']
            s.interaction_type = spec.get('interaction_type', 'content')
            s.is_mandatory = spec.get('is_mandatory', True)
            s.estimated_seconds = spec.get('estimated_seconds', 60)
            s.md_philosophy = spec.get('md_philosophy', False)
            db.flush()

        # ── Module 1 assessment bank (§O) ────────────────────────────────────
        existing_q = db.query(PpwecQuestion).filter(PpwecQuestion.module_id == m1.id).count()
        if existing_q == 0:
            for spec in QUESTIONS:
                db.add(PpwecQuestion(module_id=m1.id, **spec))

        db.commit()
        screens = db.query(PpwecScreen).filter(PpwecScreen.module_id == m1.id).count()
        questions = db.query(PpwecQuestion).filter(PpwecQuestion.module_id == m1.id).count()
        print(f'PPWEC seed complete: 18 modules cataloged; Module 1 has {screens} screens, {questions} questions.')
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    seed()
