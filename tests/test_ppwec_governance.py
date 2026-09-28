"""PPWEC governance tests — §X validation, §Y pilot/gate/freeze."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.user import User  # noqa: F401
from app.models.ppwec import PpwecModule, PpwecQuestion, PpwecScreen  # noqa: F401
from app.models.ppwec_governance import (  # noqa: F401
    PpwecPilotFeedback, PpwecReviewSignoff, PpwecDesignFreeze, PpwecValidationRun,
)


def _screen(n, **kw):
    base = dict(module_id=None, screen_number=n, section='learning', title=f'Screen {n}',
                interaction_type='content', is_mandatory=True, estimated_seconds=60)
    base.update(kw)
    return base


@pytest.fixture()
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()

    s.add(User(employee_code='GV', full_name='Gate Verifier', department='IT',
               role='admin', is_active=True, hashed_password='x'))
    s.add(User(employee_code='P1', full_name='Pilot One', department='Production',
               role='trainee', is_active=True, hashed_password='x'))
    s.flush()

    m = PpwecModule(module_number=1, title='Professionalism', theme='Foundation',
                    passing_score=80.0, badge_name='Explorer', status='draft')
    s.add(m)
    s.flush()

    # minimal valid module: 10 content screens + 5 questions with rich feedback
    for n in range(1, 11):
        s.add(PpwecScreen(**{**_screen(n), 'module_id': m.id}))
    for i in range(5):
        s.add(PpwecQuestion(
            module_id=m.id, question_type='knowledge', question_text=f'Q{i}',
            options=[{'key': 'A', 'text': 'x'}, {'key': 'B', 'text': 'y'}],
            correct_option='B', feedback_why='why', feedback_better='better',
        ))
    s.commit()
    yield s
    s.close()


def _svc(db):
    from app.services.ppwec_governance_service import PpwecGovernanceService
    return PpwecGovernanceService(db)


def _uid(db, code):
    return db.query(User).filter(User.employee_code == code).first()


class TestValidation:
    def test_valid_module_passes(self, db):
        report = _svc(db).validate_module(1)
        assert report['ok'] is True, report['errors']
        assert report['summary']['screens'] == 10
        # run persisted
        assert db.query(PpwecValidationRun).count() == 1

    def test_missing_correct_flag_fails(self, db):
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        db.add(PpwecScreen(**_screen(11, module_id=m.id, interaction_type='choice',
                                     interaction_payload={'options': [
                                         {'key': 'A', 'text': 'a', 'feedback': 'f'},
                                         {'key': 'B', 'text': 'b', 'feedback': 'g'},  # none flagged correct
                                     ]})))
        db.commit()
        report = _svc(db).validate_module(1)
        assert report['ok'] is False
        codes = {e['code'] for e in report['errors']}
        assert 'NO_CORRECT_FLAG' in codes

    def test_unscorable_interaction_too_few_options(self, db):
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        db.add(PpwecScreen(**_screen(12, module_id=m.id, interaction_type='drag_sort',
                                     interaction_payload={'options': [{'key': 'A', 'text': 'only', 'correct': True}]})))
        db.commit()
        report = _svc(db).validate_module(1)
        codes = {e['code'] for e in report['errors']}
        assert 'TOO_FEW_OPTIONS' in codes

    def test_question_missing_feedback_fails(self, db):
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        db.add(PpwecQuestion(module_id=m.id, question_text='bad', options=[
            {'key': 'A', 'text': 'x'}, {'key': 'B', 'text': 'y'}],
            correct_option='B', feedback_why=None, feedback_better=None))
        db.commit()
        report = _svc(db).validate_module(1)
        codes = {e['code'] for e in report['errors']}
        assert 'MISSING_RICH_FEEDBACK' in codes

    def test_correct_option_key_mismatch_fails(self, db):
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        db.add(PpwecQuestion(module_id=m.id, question_text='bad2', options=[
            {'key': 'A', 'text': 'x'}, {'key': 'B', 'text': 'y'}],
            correct_option='Z', feedback_why='w', feedback_better='b'))
        db.commit()
        report = _svc(db).validate_module(1)
        codes = {e['code'] for e in report['errors']}
        assert 'BAD_CORRECT_KEY' in codes

    def test_audio_without_captions_warns(self, db):
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        s = db.query(PpwecScreen).filter(PpwecScreen.screen_number == 1).first()
        s.voice_over = 'narration text'
        s.audio_url = 'ppwec/M01-S01.mp3'
        db.commit()
        report = _svc(db).validate_module(1)
        codes = {w['code'] for w in report['warnings']}
        assert 'NO_CAPTIONS' in codes


class TestPilotAndGate:
    def test_pilot_feedback_once_per_user(self, db):
        svc = _svc(db)
        uid = _uid(db, 'P1').id
        svc.submit_pilot_feedback(1, uid, engagement=5, relevance=4, realism=5, clarity=4,
                                  comments='great', function_tag='Production')
        with pytest.raises(ValueError, match='already'):
            svc.submit_pilot_feedback(1, uid, engagement=3, relevance=3, realism=3, clarity=3)

    def test_pilot_summary_aggregates(self, db):
        svc = _svc(db)
        svc.submit_pilot_feedback(1, _uid(db, 'P1').id, engagement=5, relevance=4, realism=5, clarity=4,
                                  function_tag='Production')
        u2 = User(employee_code='P2', full_name='Pilot Two', department='Quality',
                  role='trainee', is_active=True, hashed_password='x')
        db.add(u2)
        db.flush()
        svc.submit_pilot_feedback(1, u2.id, engagement=3, relevance=3, realism=4, clarity=4,
                                  function_tag='Quality')
        summary = svc.pilot_summary(1)
        assert summary['responses'] == 2
        assert summary['avg']['engagement'] == 4.0
        assert set(summary['functions_covered']) == {'Production', 'Quality'}

    def test_gate_pending_then_open(self, db):
        svc = _svc(db)
        st = svc.gate_status(1)
        assert all(v == 'pending' for v in st['stages'].values())
        assert st['gate_open'] is False

        # pilot feedback required before pilot sign-off
        with pytest.raises(ValueError, match='pilot feedback'):
            svc.record_signoff(1, _uid(db, 'GV'), 'pilot', decision='approved')

        svc.submit_pilot_feedback(1, _uid(db, 'P1').id, 5, 5, 5, 5) if False else \
            svc.submit_pilot_feedback(1, _uid(db, 'P1').id, engagement=5, relevance=5,
                                      realism=5, clarity=5)

        for stage in ('content_owner', 'md_leadership', 'ux', 'pilot'):
            svc.record_signoff(1, _uid(db, 'GV'), stage, decision='approved')
        st = svc.gate_status(1)
        assert st['gate_open'] is True
        # content_owner approval advanced the §29 status machine
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        assert m.status in ('reviewed', 'approved', 'final')

    def test_freeze_blocked_until_gate_green(self, db):
        svc = _svc(db)
        with pytest.raises(ValueError, match='Gate not green'):
            svc.set_design_freeze(1, _uid(db, 'GV'), True)

    def test_freeze_toggle(self, db):
        svc = _svc(db)
        uid = _uid(db, 'P1').id
        svc.submit_pilot_feedback(1, uid, engagement=5, relevance=5, realism=5, clarity=5)
        for stage in ('content_owner', 'md_leadership', 'ux', 'pilot'):
            svc.record_signoff(1, _uid(db, 'GV'), stage, decision='approved')
        out = svc.set_design_freeze(1, _uid(db, 'GV'), True)
        assert out['design_frozen'] is True
        m = db.query(PpwecModule).filter(PpwecModule.module_number == 1).first()
        assert m.status == 'final'
        assert db.query(PpwecDesignFreeze).count() == 1

        svc.set_design_freeze(1, _uid(db, 'GV'), False)
        assert svc.gate_status(1)['design_frozen'] is False
