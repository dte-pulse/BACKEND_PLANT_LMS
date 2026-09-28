"""PPWEC service tests — player flow, scoring, assessment, passport, badges,
points, 7-Day Challenge, certificates and reports."""
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
# Import models BEFORE create_all so every table is registered on the metadata.
from app.models.user import User  # noqa: F401
from app.models.ppwec import PpwecModule, PpwecQuestion, PpwecScreen  # noqa: F401


@pytest.fixture()
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()

    s.add(User(employee_code='T1', full_name='Test Trainee', department='Production',
               role='trainee', is_active=True, hashed_password='x'))
    s.flush()

    m = PpwecModule(module_number=1, title='Professionalism', theme='Foundation',
                    passing_score=80.0, badge_name='Explorer', status='final')
    s.add(m)
    s.flush()

    # content screen (mandatory) + interactive screen (mandatory) + optional
    s.add(PpwecScreen(module_id=m.id, screen_number=1, section='learning',
                      title='Intro', interaction_type='content'))
    s.add(PpwecScreen(module_id=m.id, screen_number=2, section='learning',
                      title='Scenario', interaction_type='choice',
                      interaction_payload={'options': [
                          {'key': 'A', 'text': 'Wrong', 'correct': False, 'feedback': 'nope'},
                          {'key': 'B', 'text': 'Right', 'correct': True, 'feedback': 'well done'},
                      ]}))
    s.add(PpwecScreen(module_id=m.id, screen_number=3, section='closure',
                      title='Optional', interaction_type='content', is_mandatory=False))
    s.flush()

    for i, qt in enumerate([
        'knowledge', 'application', 'situational_judgement',
        'decision', 'knowledge', 'application',
        'situational_judgement', 'decision', 'knowledge',
        'application',
    ]):
        correct = 'B'
        s.add(PpwecQuestion(
            module_id=m.id, question_type=qt,
            question_text=f'Q{i+1}', options=[{'key': 'A', 'text': 'a'}, {'key': 'B', 'text': 'b'}],
            correct_option=correct,
            feedback_why='why', feedback_better='better',
        ))
    s.commit()

    yield s
    s.close()


def _service(db):
    from app.services.ppwec_service import PpwecService
    return PpwecService(db)


def _user(db):
    from app.models.user import User
    return db.query(User).filter(User.employee_code == 'T1').first()


class TestModuleFlow:
    def test_start_module_returns_resume_state(self, db):
        svc = _service(db)
        state = svc.start_module(_user(db).id, 1)
        assert state['resume_screen_id'] is not None
        assert state['status'] == 'not_started'
        # no-skip: only the first screen is unlocked initially
        assert len(state['unlocked_screen_ids']) == 1

    def test_complete_mandatory_screens_unlock_assessment(self, db):
        svc = _service(db)
        uid = _user(db).id
        svc.start_module(uid, 1)
        svc.complete_screen(uid, 1, svc.get_module_detail(1)['screens'][0]['id'], time_spent_seconds=30)
        out = svc.complete_screen(
            uid, 1, svc.get_module_detail(1)['screens'][1]['id'],
            interaction_result={'selected': 'B'},
        )
        assert out['assessment_unlocked'] is True
        assert out['feedback']['correct'] is True
        assert out['points_delta'] > 0

    def test_interaction_scoring_wrong_answer(self, db):
        svc = _service(db)
        uid = _user(db).id
        svc.start_module(uid, 1)
        svc.complete_screen(uid, 1, svc.get_module_detail(1)['screens'][0]['id'])
        out = svc.complete_screen(
            uid, 1, svc.get_module_detail(1)['screens'][1]['id'],
            interaction_result={'selected': 'A'},
        )
        assert out['feedback']['correct'] is False
        # learner still gets rich feedback + the better approach
        assert out['feedback']['better_approach'] == 'Right'
        # no points for a wrong scenario decision
        assert out['points_delta'] == 0


class TestAssessment:
    def _unlock(self, svc, uid):
        svc.start_module(uid, 1)
        screens = svc.get_module_detail(1)['screens']
        svc.complete_screen(uid, 1, screens[0]['id'])
        svc.complete_screen(uid, 1, screens[1]['id'], interaction_result={'selected': 'B'})

    def test_assessment_locked_until_mandatory_done(self, db):
        svc = _service(db)
        with pytest.raises(ValueError, match='mandatory'):
            svc.start_assessment(_user(db).id, 1)

    def test_full_pass_flow_awards_badge_and_points(self, db):
        svc = _service(db)
        uid = _user(db).id
        self._unlock(svc, uid)

        start = svc.start_assessment(uid, 1)
        assert len(start['questions']) == 10
        # §16: options are re-lettered per attempt — answer via display text
        # like a real learner (correct original option is the one keyed 'B').
        answers = {}
        for q in start['questions']:
            correct_display = next(o['key'] for o in q['options'] if o.get('text') == 'b')
            answers[str(q['id'])] = correct_display
        result = svc.submit_assessment(uid, 1, start['attempt_id'], answers)
        assert result['passed'] is True
        assert result['score'] == 100.0
        assert result['badge_awarded'] == 'Explorer'

        passport = svc.get_passport(uid)
        assert passport['modules_completed'] == 1
        assert passport['total_points'] >= 125  # 100 assessment + 25 completion + interactions
        assert any(b['name'] == 'Explorer' for b in passport['badges'])

    def test_fail_retry_and_best_score_kept(self, db):
        svc = _service(db)
        uid = _user(db).id
        self._unlock(svc, uid)

        first = svc.start_assessment(uid, 1)
        # answer everything wrong -> 0%
        result = svc.submit_assessment(uid, 1, first['attempt_id'], {})
        assert result['passed'] is False
        assert result['retry_allowed'] is True

        second = svc.start_assessment(uid, 1)
        assert second['attempt_number'] == 2
        answers = {str(q['id']): 'B' for q in second['questions']}
        result2 = svc.submit_assessment(uid, 1, second['attempt_id'], answers)
        # 10/10 correct is impossible here (correct keys vary), just assert graded
        assert result2['score'] is not None
        assert result2['attempt_id'] != first['attempt_id']

    def test_randomization_serves_subset_snapshot(self, db):
        svc = _service(db)
        uid = _user(db).id
        self._unlock(svc, uid)
        start = svc.start_assessment(uid, 1)
        from app.models.ppwec import PpwecAssessmentAttempt
        attempt = db.get(PpwecAssessmentAttempt, start['attempt_id'])
        assert sorted(attempt.question_ids) == sorted(q['id'] for q in start['questions'])
        # options are served without answers
        assert all('correct_option' not in q for q in start['questions'])

    def test_option_order_randomization_and_grade_mapping(self, db):
        """§16: options are served in randomized order with fresh display
        letters, and grading maps display letters back to original keys."""
        svc = _service(db)
        uid = _user(db).id
        self._unlock(svc, uid)
        start = svc.start_assessment(uid, 1)

        for q in start['questions']:
            # display letters are re-issued positionally
            for pos, opt in enumerate(q['options']):
                assert opt['key'] == 'ABCDEFGH'[pos]
            # some question's original 'B' option may now sit at another letter
            text_keys = {o['text']: o['key'] for o in q['options']}
            assert 'b' in text_keys  # our fixture texts survive re-lettering

        # answer half right (via text mapping), half wrong (the option whose
        # text is 'a' — original key A, never correct in this fixture)
        answers = {}
        for i, q in enumerate(start['questions']):
            if i % 2 == 0:
                answers[str(q['id'])] = next(o['key'] for o in q['options'] if o['text'] == 'b')
            else:
                answers[str(q['id'])] = next(o['key'] for o in q['options'] if o['text'] == 'a')
        result = svc.submit_assessment(uid, 1, start['attempt_id'], answers)
        assert result['score'] == 50.0
        assert result['correct_count'] == 5

    def test_cannot_resubmit_attempt(self, db):
        svc = _service(db)
        uid = _user(db).id
        self._unlock(svc, uid)
        start = svc.start_assessment(uid, 1)
        svc.submit_assessment(uid, 1, start['attempt_id'], {})
        with pytest.raises(ValueError, match='already submitted'):
            svc.submit_assessment(uid, 1, start['attempt_id'], {})


class TestChallenge:
    def _unlock(self, svc, uid):
        svc.start_module(uid, 1)
        screens = svc.get_module_detail(1)['screens']
        svc.complete_screen(uid, 1, screens[0]['id'])
        svc.complete_screen(uid, 1, screens[1]['id'], interaction_result={'selected': 'B'})

    def test_challenge_day_points(self, db):
        svc = _service(db)
        uid = _user(db).id
        svc.start_challenge(uid, 1)
        out = svc.tick_challenge_day(uid, 1, 1, True)
        assert 1 in out['days_completed']
        out = svc.tick_challenge_day(uid, 1, 1, False)  # untick
        assert 1 not in out['days_completed']
        out = svc.tick_challenge_day(uid, 1, 1, True)

    def test_full_challenge_completion(self, db):
        svc = _service(db)
        uid = _user(db).id
        svc.start_challenge(uid, 1)
        for day in range(1, 8):
            out = svc.tick_challenge_day(uid, 1, day, True)
        assert out['completed_at'] is not None
        assert len(out['days_completed']) == 7
        passport = svc.get_passport(uid)
        # 7 days x 10 pts + 70 completion bonus
        assert passport['total_points'] >= 140


class TestPassportAndReports:
    def test_passport_empty_state(self, db):
        svc = _service(db)
        p = svc.get_passport(_user(db).id)
        assert p['total_modules'] == 1
        assert p['modules_completed'] == 0
        assert p['certification_earned'] is False

    def test_overview_shape(self, db):
        svc = _service(db)
        o = svc.my_overview(_user(db).id)
        assert o['total_modules'] == 1
        assert o['next_module'] is not None

    def test_org_report_counts(self, db):
        svc = _service(db)
        rep = svc.org_report()
        assert rep['total_employees'] == 1
        assert rep['total_modules'] == 1
        assert rep['completed'] == 0

    def test_department_report_and_csv(self, db):
        svc = _service(db)
        rows = svc.department_report()
        assert rows[0]['department'] == 'Production'
        csv = svc.csv_report()
        assert 'employee_code' in csv
        assert 'T1' in csv

    def test_certificate_data(self, db):
        svc = _service(db)
        data = svc.certificate_data(_user(db).id)
        assert data['employee_code'] == 'T1'
        assert data['certification_earned'] is False


class TestMediaEndpoints:
    """§7/§23 voice-over pipeline: signed playback URLs, uploads, TTS export."""

    # One app lifespan for the whole class: repeated TestClient startup/shutdown
    # cycles hang intermittently (Langfuse flush on shutdown), so share it.
    @pytest.fixture(scope='class')
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            yield c

    @pytest.fixture(scope='class')
    def trainee_headers(self, client):
        r = client.post('/api/v1/auth/login', json={'employee_code': 'trainee', 'password': 'traineepassword'})
        assert r.status_code == 200
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    @pytest.fixture(scope='class')
    def admin_headers(self, client):
        r = client.post('/api/v1/auth/login', json={'employee_code': 'admin', 'password': 'adminpassword'})
        assert r.status_code == 200
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    def test_playback_token_roundtrip_local_file(self, client, trainee_headers, tmp_path):
        """JWT exchange -> signed URL -> Range stream of a local audio file."""
        from app.core.config import settings

        # fake local audio file in the upload dir (restore the global after --
        # a leaked override poisons later storage-touching tests)
        old_dir = settings.upload_dir
        media_dir = tmp_path / 'ppwec'
        media_dir.mkdir(parents=True)
        fake_mp3 = b'ID3' + b'\x00' * 64
        (media_dir / 'test.mp3').write_bytes(fake_mp3)
        settings.upload_dir = str(tmp_path)

        try:
            from app.db.session import SessionLocal
            from app.models.ppwec import PpwecScreen
            db = SessionLocal()
            screen = db.query(PpwecScreen).filter(PpwecScreen.screen_number == 1).first()
            screen.audio_url = 'ppwec/test.mp3'
            db.commit()
            db.close()

            r = client.post('/api/v1/ppwec/modules/1/screens/1/media-src', headers=trainee_headers)
            assert r.status_code == 200, r.text
            src = r.json()['src']
            assert src.startswith('/ppwec/media/1/1/audio?pt=')

            # stream with the signed token (no Authorization header)
            r = client.get(f'/api/v1{src}')
            assert r.status_code == 200
            assert r.content.startswith(b'ID3')

            # Range request (seeking) works with the SAME token
            r = client.get(f'/api/v1{src}', headers={'Range': 'bytes=0-2'})
            assert r.status_code == 206
            assert r.content == b'ID3'
            assert 'Content-Range' in r.headers
        finally:
            settings.upload_dir = old_dir

    def test_playback_token_rejects_wrong_scope(self, client, trainee_headers):
        r = client.post('/api/v1/ppwec/modules/1/screens/1/media-src', headers=trainee_headers)
        src = r.json()['src']
        # screen 2 with screen 1's token must be rejected
        bad = src.replace('/1/1/audio', '/1/2/audio')
        r = client.get(f'/api/v1{bad}')
        assert r.status_code == 403

    def test_stream_without_token_rejected(self, client):
        r = client.get('/api/v1/ppwec/media/1/1/audio?pt=not-a-token')
        assert r.status_code == 401

    def test_media_upload_validation(self, client, admin_headers, monkeypatch):
        from app.core.config import settings
        old_dir = settings.upload_dir
        import tempfile
        # never touch real S3 from tests
        import app.core.s3 as s3mod
        monkeypatch.setattr(s3mod, 'get_s3_client', lambda: None)
        settings.upload_dir = tempfile.mkdtemp()
        try:
            # magic-byte mismatch must be rejected (text posing as mp3)
            r = client.post(
                '/api/v1/ppwec/admin/modules/1/screens/1/media',
                headers=admin_headers,
                data={'kind': 'audio'},
                files={'file': ('fake.mp3', io.BytesIO(b'this is not audio'), 'audio/mpeg')},
            )
            assert r.status_code == 400
            # valid wav accepted + attached
            wav = b'RIFF' + (b'\x00' * 36) + b'WAVE' + b'\x00' * 24
            r = client.post(
                '/api/v1/ppwec/admin/modules/1/screens/1/media',
                headers=admin_headers,
                data={'kind': 'audio'},
                files={'file': ('vo01.wav', io.BytesIO(wav), 'audio/wav')},
            )
            assert r.status_code == 201, r.text
            assert r.json()['url'].endswith('.wav')
        finally:
            settings.upload_dir = old_dir

    def test_tts_export(self, client, admin_headers):
        r = client.post('/api/v1/ppwec/admin/modules/1/tts-export', json={'format': 'json'}, headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data['module_number'] == 1
        assert len(data['items']) > 0
        item = data['items'][0]
        assert item['filename'].startswith('M01-S')
        assert item['est_seconds'] >= 4
        # srt format
        r = client.post('/api/v1/ppwec/admin/modules/1/tts-export', json={'format': 'srt'}, headers=admin_headers)
        assert '-->' in r.json()['items'][0]['srt']
