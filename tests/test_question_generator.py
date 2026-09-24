"""
QuestionGeneratorAgent — regression tests for the repeat-question fix.

Prod bug (confirmed): when every MCQ of the planned difficulty had already
been shown, ``generate`` REUSED a seen MCQ — so a learner kept getting the
same question (e.g. chunk 446 served mcq 1279 three times in a row). The
agent must now:

- serve an unseen bank MCQ when one exists,
- generate a fresh DYNAMIC question when the difficulty pool is exhausted
  (and an LLM is available),
- only fall back to the bank (any difficulty, unseen first) when no LLM is
  available — never repeat a question the learner has already seen.
"""
import types

import pytest

from app.agents.question_generator_agent import QuestionGeneratorAgent


class _Mcq:
    def __init__(self, mid, difficulty='medium', qtype='objective', question='Q'):
        self.id = mid
        self.difficulty = difficulty
        self.type = qtype
        self.question = question
        self.options = {'A': 'opt A', 'B': 'opt B', 'C': 'opt C', 'D': 'opt D'}
        self.correct_option = 'A'
        self.explanation = 'explanation'


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    """Scripted DB: each ``query()`` call returns the next row-set, so the
    difficulty-filtered bank query and the any-difficulty widening query can
    return different (realistic) pools."""

    def __init__(self, *row_sets):
        self._row_sets = list(row_sets) or [[]]
        self._calls = 0

    def query(self, model):
        rows = self._row_sets[self._calls % len(self._row_sets)]
        self._calls += 1
        return _FakeQuery(rows)

    def add(self, obj):  # dynamic questions are persisted best-effort
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def rollback(self):
        pass


class _FakeLlm:
    def __init__(self, model, json_result=None):
        self.model = model
        self._json = json_result

    def generate_json(self, prompt, user_id=0, operation='llm_json'):
        return self._json


def _chunk():
    return types.SimpleNamespace(
        id=446, document_id=11, topic_id=3, page_no=1,
        content='The Low-Level Design document specifies how each component will be built.',
    )


def _agent(db, llm, monkeypatch):
    agent = QuestionGeneratorAgent(db)
    monkeypatch.setattr(agent, 'llm', lambda: llm)
    return agent


class TestNeverRepeatsSeenQuestions:
    def test_serves_unseen_bank_mcq_when_available(self, monkeypatch):
        bank = [_Mcq(1, 'easy'), _Mcq(2, 'easy')]
        llm = _FakeLlm('gemini-2.5-flash', {'question': 'dynamic', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'}, 'correct_option': 'A'})
        agent = _agent(_FakeDb(bank), llm, monkeypatch)

        q = agent.generate(user_id=15, chunk=_chunk(), difficulty='easy', prefer_format='objective', exclude_mcq_ids=[1])

        assert q['is_dynamic'] is False
        assert q['mcq_id'] == 2       # the unseen one, not the shown 1

    def test_generates_dynamic_question_when_pool_exhausted(self, monkeypatch):
        # Only ONE easy MCQ exists and it has already been shown → the old code
        # reused it (the prod repeat bug). With an LLM we must generate fresh.
        bank = [_Mcq(1279, 'easy')]
        llm = _FakeLlm('gemini-2.5-flash', {
            'question': 'A brand new easy question', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
            'correct_option': 'B', 'explanation': 'why',
        })
        agent = _agent(_FakeDb(bank), llm, monkeypatch)

        q = agent.generate(user_id=15, chunk=_chunk(), difficulty='easy', prefer_format='objective', exclude_mcq_ids=[1279])

        assert q['is_dynamic'] is True
        assert q['question'] == 'A brand new easy question'
        assert q['difficulty'] == 'easy'

    def test_widens_to_unseen_any_difficulty_without_llm(self, monkeypatch):
        # No LLM: must not repeat the seen question — widen to an unseen bank
        # MCQ from ANY difficulty instead.
        # Row-set 1: the 'easy' difficulty-filtered bank query. Row-set 2: the
        # any-difficulty widening query.
        llm = _FakeLlm(None)  # no API key → mock mode
        agent = _agent(_FakeDb([_Mcq(1279, 'easy')], [_Mcq(1279, 'easy'), _Mcq(1130, 'medium')]), llm, monkeypatch)

        q = agent.generate(user_id=15, chunk=_chunk(), difficulty='easy', prefer_format='objective', exclude_mcq_ids=[1279])

        assert q['is_dynamic'] is False
        assert q['mcq_id'] == 1130    # unseen medium, NOT the shown easy 1279
        assert q['difficulty'] == 'medium'

    def test_reuses_only_as_last_resort_when_everything_shown_and_no_llm(self, monkeypatch):
        # No LLM AND every bank MCQ for the chunk has been shown: the only
        # remaining option is the content-grounded seen MCQ — better than the
        # generic deterministic fallback question.
        llm = _FakeLlm(None)
        agent = _agent(_FakeDb([_Mcq(1279, 'easy')], [_Mcq(1279, 'easy')]), llm, monkeypatch)

        q = agent.generate(user_id=15, chunk=_chunk(), difficulty='easy', prefer_format='objective', exclude_mcq_ids=[1279])

        assert q['is_dynamic'] is False
        assert q['mcq_id'] == 1279
        assert q['question'] == 'Q'
