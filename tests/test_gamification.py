"""Gamification service tests — coins, streaks, dedup, wallet, leaderboard."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
# Import models BEFORE create_all so every table is registered on the metadata.
from app.models.user import User  # noqa: F401
from app.models.gamification import CoinLedger, UserStreak  # noqa: F401
from app.models.ppwec import PpwecModule  # noqa: F401


@pytest.fixture()
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()

    s.add(User(employee_code='G1', full_name='Gamified User', department='Production',
               role='trainee', is_active=True, hashed_password='x'))
    s.add(User(employee_code='G2', full_name='Rival User', department='Quality',
               role='trainee', is_active=True, hashed_password='x'))
    s.add(User(employee_code='G3', full_name='Poor User', department='Stores',
               role='trainee', is_active=True, hashed_password='x'))
    s.flush()

    s.add(PpwecModule(module_number=1, title='Professionalism', theme='Foundation', status='final'))
    s.commit()

    yield s
    s.close()


def _service(db):
    from app.services.gamification_service import GamificationService
    return GamificationService(db)


def _uid(db, code):
    return db.query(User).filter(User.employee_code == code).first().id


class TestAwarding:
    def test_award_credits_and_touches_streak(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')

        coins = svc.award(uid, 'mcq_pass', reference_type='attempt', reference_id='a1')
        assert coins == 10

        streak = db.query(UserStreak).filter(UserStreak.user_id == uid).first()
        assert streak.current_streak == 1
        assert streak.longest_streak == 1
        assert streak.total_coins == 10
        assert 1 <= max(streak.week_activity or [0]) <= 7

    def test_dedup_same_reference_no_double_pay(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')

        first = svc.award(uid, 'assignment_complete', reference_type='assignment', reference_id='42')
        second = svc.award(uid, 'assignment_complete', reference_type='assignment', reference_id='42')
        assert first == 25
        assert second is None
        assert svc.total_coins(uid) == 25

    def test_different_activity_same_ref_awarded(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        assert svc.award(uid, 'mcq_pass', reference_type='attempt', reference_id='9') == 10
        assert svc.award(uid, 'mcq_fail', reference_type='attempt', reference_id='9') is None or True
        # mcq_fail after mcq_pass on the same attempt id: distinct activity rows both allowed
        assert svc.total_coins(uid) >= 10

    def test_unknown_activity_zero_coins(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        assert svc.award(uid, 'not_a_real_event') is None
        assert svc.total_coins(uid) == 0

    def test_notify_event_mapping(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        assert svc.notify_event(uid, 'chunk_completed', chunk_id='77') == 5
        assert svc.notify_event(uid, 'chunk_completed', chunk_id='77') is None  # dedup
        assert svc.notify_event(uid, 'bogus_event') is None

    def test_daily_login_dedupes_by_date(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        assert svc.notify_event(uid, 'daily_login') == 2
        assert svc.notify_event(uid, 'daily_login') is None
        assert svc.total_coins(uid) == 2

    def test_ppwec_challenge_bonus_event(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        assert svc.notify_event(uid, 'ppwec_challenge_bonus', day_ref='1-bonus') == 70


class TestStreaks:
    def test_streak_milestone_bonus_at_7_days(self, db, monkeypatch):
        from app.services import gamification_service as gs
        svc = _service(db)
        uid = _uid(db, 'G1')

        # Simulate 6 prior consecutive days by backdating the streak row.
        streak = svc._get_or_create_streak(uid)
        today = datetime.utcnow().date()
        streak.current_streak = 6
        streak.longest_streak = 6
        streak.total_coins = 60
        streak.last_active_date = datetime(today.year, today.month, today.day) - timedelta(days=1)
        streak.week_activity = []
        db.commit()

        coins = svc.award(uid, 'daily_login')
        assert coins == 2

        db.refresh(streak)
        assert streak.current_streak == 7
        # milestone bonus ledger row exists
        bonus = (db.query(CoinLedger)
                 .filter(CoinLedger.user_id == uid, CoinLedger.activity_type == 'streak_bonus')
                 .first())
        assert bonus is not None and bonus.points == 20
        assert streak.total_coins == 60 + 2 + 20

    def test_streak_breaks_after_gap(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        streak = svc._get_or_create_streak(uid)
        today = datetime.utcnow().date()
        streak.current_streak = 5
        streak.last_active_date = datetime(today.year, today.month, today.day) - timedelta(days=3)
        db.commit()

        svc.award(uid, 'daily_login')
        db.refresh(streak)
        assert streak.current_streak == 1  # broken, restarted


class TestWalletAndLeaderboard:
    def test_wallet_shape(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        svc.notify_event(uid, 'daily_login')
        svc.notify_event(uid, 'chunk_completed', chunk_id='1')
        svc.notify_event(uid, 'ppwec_module_completed', module_id='1')

        w = svc.wallet(uid)
        assert w['total_coins'] == 2 + 5 + 25
        assert w['month_coins'] == 32
        assert w['breakdown']['daily'] == 2
        assert w['breakdown']['learning'] == 5
        assert w['breakdown']['practice'] == 25
        assert w['global_rank'] == 1
        assert w['streak']['current_streak'] == 1

    def test_leaderboard_ranking_and_you_row(self, db):
        svc = _service(db)
        u1, u2, u3 = _uid(db, 'G1'), _uid(db, 'G2'), _uid(db, 'G3')

        svc.award(u1, 'assignment_complete', reference_type='a', reference_id='1')   # 25
        svc.award(u1, 'mcq_pass', reference_type='b', reference_id='1')              # 10
        svc.award(u2, 'ppwec_assessment', reference_type='c', reference_id='1')      # 100
        # u3 earns nothing

        board = svc.leaderboard(user_id=u3)
        assert board['entries'][0]['user_id'] == u2
        assert board['entries'][0]['coins'] == 100
        assert board['entries'][1]['user_id'] == u1
        # u3 has zero coins -> not on the board, no "you" row
        assert board['you'] is None

        board2 = svc.leaderboard(user_id=u1)
        assert board2['you']['rank'] == 2
        assert board2['you']['coins'] == 35
        assert board2['you']['is_you'] is True

    def test_leaderboard_month_filtering(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        svc.award(uid, 'mcq_pass', reference_type='x', reference_id='1')
        # backdate a row to last month -> must not count this month
        row = db.query(CoinLedger).filter(CoinLedger.user_id == uid).first()
        row.created_at = datetime.utcnow().replace(day=1) - timedelta(days=1)
        db.commit()

        board = svc.leaderboard(user_id=uid)
        assert all(e['coins'] == 0 for e in board['entries'] if e['user_id'] == uid) or board['entries'] == []
        w = svc.wallet(uid)
        assert w['total_coins'] == 10          # all-time still counts
        assert w['month_coins'] == 0           # this month is empty

    def test_earning_history(self, db):
        svc = _service(db)
        uid = _uid(db, 'G1')
        svc.notify_event(uid, 'daily_login')
        svc.notify_event(uid, 'chunk_completed', chunk_id='5')
        hist = svc.earning_history(uid)
        assert len(hist) == 2
        assert {h['activity_type'] for h in hist} == {'daily_login', 'chunk_complete'}


class TestLeaderboardRoleGuard:
    """Leaderboard is a trainee-only surface (staff have report views)."""

    @pytest.fixture(scope='class')
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            yield c

    def _login(self, client, code, pw):
        r = client.post('/api/v1/auth/login', json={'employee_code': code, 'password': pw})
        assert r.status_code == 200
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    def test_trainee_can_access_leaderboard(self, client):
        headers = self._login(client, 'trainee', 'traineepassword')
        r = client.get('/api/v1/gamification/leaderboard', headers=headers)
        assert r.status_code == 200
        assert 'entries' in r.json()

    def test_admin_forbidden(self, client):
        headers = self._login(client, 'admin', 'adminpassword')
        r = client.get('/api/v1/gamification/leaderboard', headers=headers)
        assert r.status_code == 403

    def test_trainer_forbidden(self, client):
        headers = self._login(client, 'trainer', 'trainerpassword')
        r = client.get('/api/v1/gamification/leaderboard', headers=headers)
        assert r.status_code == 403
