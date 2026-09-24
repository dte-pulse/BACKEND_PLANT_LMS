"""RecommenderAgent — the learner-facing capability dashboard.

Aggregates the concept-mastery rows into:
- summary stats (mastered / proficient / learning / novice, weak, critical)
- per-topic and per-document roll-ups
- a weakness heatmap (concepts below the weak threshold, with LLM insights)
- strengths (mastered concepts)
- an LLM-generated, prioritized study plan (cached 60s — the plan is stable
  between answers and the LLM call is the expensive part)
"""
import logging

from sqlalchemy.orm import Session

from app.agents.base_agent import (
    BaseAgent,
    mastery_level_for_score,
    WEAK_THRESHOLD,
    CRITICAL_THRESHOLD,
)
from app.models.user_concept_mastery import UserConceptMastery

logger = logging.getLogger(__name__)

STUDY_PLAN_CACHE_TTL = 60  # seconds

# Cap the replayed trend series — the trajectory matters, not every dot.
MAX_HISTORY_POINTS = 50


class RecommenderAgent(BaseAgent):
    def __init__(self, db: Session):
        super().__init__(db)

    def build_profile(self, user_id: int, document_id: int | None = None) -> dict:
        from app.models.chunk import Chunk
        from app.models.parent_chunk import ParentChunk
        from app.models.document import Document
        from app.models.topic import Topic
        from sqlalchemy.orm import defer

        q = self.db.query(UserConceptMastery).filter(UserConceptMastery.user_id == user_id)
        if document_id:
            q = q.filter(UserConceptMastery.document_id == document_id)
        concepts = q.order_by(UserConceptMastery.score.asc()).all()

        if not concepts:
            return self._empty_profile(user_id, document_id)

        # Batch-fetch the referenced entities in 3 queries.
        child_ids = [c.child_chunk_id for c in concepts]
        parent_ids = [c.parent_chunk_id for c in concepts]
        doc_ids = {c.document_id for c in concepts}
        topic_ids = {c.topic_id for c in concepts}

        children = {
            ch.id: ch for ch in self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
                Chunk.id.in_(child_ids)).all()
        } if child_ids else {}
        parents = {
            p.id: p for p in self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
                ParentChunk.id.in_(parent_ids)).all()
        } if parent_ids else {}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
        topics = {t.id: t for t in self.db.query(Topic).filter(Topic.id.in_(topic_ids)).all()} if topic_ids else {}

        # ── Weakness heatmap + strengths ─────────────────────────────────────
        weaknesses = []
        strengths = []
        for c in concepts:
            child = children.get(c.child_chunk_id)
            parent = parents.get(c.parent_chunk_id)
            doc = docs.get(c.document_id)
            topic = topics.get(c.topic_id)
            item = {
                'child_chunk_id': c.child_chunk_id,
                'parent_chunk_id': c.parent_chunk_id,
                'page_no': child.page_no if child else None,
                'section_title': parent.title if parent else f'Section {c.parent_chunk_id}',
                'document_id': c.document_id,
                'document_title': doc.title if doc else f'Document #{c.document_id}',
                'topic_id': c.topic_id,
                'topic_title': topic.title if topic else f'Topic #{c.topic_id}',
                'score': round(c.score, 1),
                'mastery_level': c.mastery_level,
                'attempts': c.attempts,
                'consecutive_correct': c.consecutive_correct,
                'insight': c.insight,
                'needs_review': c.needs_review,
            }
            if c.score < WEAK_THRESHOLD:
                item['severity'] = 'critical' if c.score < CRITICAL_THRESHOLD else 'weak'
                weaknesses.append(item)
            if c.mastery_level in ('mastered', 'proficient'):
                strengths.append(item)

        # ── Roll-ups ─────────────────────────────────────────────────────────
        by_topic: dict[int, list] = {}
        by_doc: dict[int, list] = {}
        for c in concepts:
            by_topic.setdefault(c.topic_id, []).append(c)
            by_doc.setdefault(c.document_id, []).append(c)

        topics_out = []
        for t_id, rows in by_topic.items():
            avg = sum(r.score for r in rows) / len(rows)
            topic = topics.get(t_id)
            topics_out.append({
                'topic_id': t_id,
                'title': topic.title if topic else f'Topic #{t_id}',
                'avg_score': round(avg, 1),
                'concepts': len(rows),
                'weak_concepts': sum(1 for r in rows if r.score < WEAK_THRESHOLD),
                'is_critical': avg < CRITICAL_THRESHOLD,
                'mastery': mastery_level_for_score(avg),
            })

        docs_out = []
        for d_id, rows in by_doc.items():
            avg = sum(r.score for r in rows) / len(rows)
            doc = docs.get(d_id)
            weakest = min(rows, key=lambda r: r.score)
            docs_out.append({
                'document_id': d_id,
                'title': doc.title if doc else f'Document #{d_id}',
                'avg_score': round(avg, 1),
                'total_concepts': len(rows),
                'mastered_concepts': sum(1 for r in rows if r.mastery_level == 'mastered'),
                'weakest_section': parents.get(weakest.parent_chunk_id).title if parents.get(weakest.parent_chunk_id) else None,
                'weakest_score': round(weakest.score, 1),
            })

        # ── LLM study plan (cached, scoped by document) ─────────────────────
        study_plan = self._study_plan(user_id, weaknesses, document_id)

        summary = {
            'total_concepts': len(concepts),
            'mastered': sum(1 for c in concepts if c.mastery_level == 'mastered'),
            'proficient': sum(1 for c in concepts if c.mastery_level == 'proficient'),
            'learning': sum(1 for c in concepts if c.mastery_level == 'learning'),
            'novice': sum(1 for c in concepts if c.mastery_level == 'novice'),
            'weak_concepts': len(weaknesses),
            'critical_concepts': sum(1 for w in weaknesses if w['severity'] == 'critical'),
            'avg_score': round(sum(c.score for c in concepts) / len(concepts), 1),
        }

        return {
            'user_id': user_id,
            'document_id': document_id,
            'summary': summary,
            'documents': docs_out,
            'topics': topics_out,
            'weaknesses': weaknesses,
            'strengths': strengths,
            'study_plan': study_plan,
        }

    # ── Mastery history (score over time per concept) ───────────────────────

    def build_mastery_history(self, user_id: int, document_id: int | None = None) -> dict:
        """Reconstruct each concept's score trajectory from ChildChunkAttempt.

        The mastery EMA is deterministic, so replaying every attempt in
        chronological order reproduces the exact score the learner saw after
        each answer — no separate history table needed. Points carry the
        attempt result and difficulty so the frontend can render a trend line
        with correct/wrong markers.

        Note: the replay starts from score 0.0, matching the initial mastery
        row — attempts recorded before the concept-mastery feature existed are
        included in the trajectory even though they never updated a live row
        (a bounded, harmless divergence on legacy data).

        Also returns a ``document_average`` series: the carried-forward mean of
        every concept's score at each attempt index, so the frontend can overlay
        the document's overall trajectory on any single concept's chart.
        """
        from app.models.child_chunk_attempt import ChildChunkAttempt
        from app.models.chunk import Chunk
        from app.models.parent_chunk import ParentChunk
        from app.models.document import Document
        from app.agents.weakness_agent import ema_update
        from sqlalchemy.orm import defer

        q = self.db.query(ChildChunkAttempt).filter(ChildChunkAttempt.user_id == user_id)
        if document_id:
            q = q.filter(ChildChunkAttempt.document_id == document_id)
        attempts = q.order_by(ChildChunkAttempt.id.asc()).all()
        if not attempts:
            return {'user_id': user_id, 'document_id': document_id,
                    'document_average': {'attempts': 0, 'points': []}, 'concepts': []}

        # Group attempts per concept, already in chronological order.
        by_concept: dict[int, list] = {}
        for a in attempts:
            by_concept.setdefault(a.child_chunk_id, []).append(a)

        # Batch-fetch referenced entities (3 queries, no N+1).
        child_ids = list(by_concept.keys())
        children = {
            c.id: c for c in self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
                Chunk.id.in_(child_ids)).all()
        } if child_ids else {}
        parent_ids = {c.parent_chunk_id for c in children.values() if c.parent_chunk_id}
        parents = {
            p.id: p for p in self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
                ParentChunk.id.in_(parent_ids)).all()
        } if parent_ids else {}
        doc_ids = {a.document_id for a in attempts}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}

        # Full (uncapped) score trajectories per concept — the document average
        # needs every point even when a concept's output series gets capped.
        full_trajectories: dict[int, list[float]] = {}
        for cid, rows in by_concept.items():
            score = 0.0
            traj = []
            for a in rows:
                score = ema_update(score, a.is_correct)
                traj.append(round(score, 1))
            full_trajectories[cid] = traj

        # ── Document-average trajectory ─────────────────────────────────────
        # Carried-forward mean across concepts at each attempt index: concepts
        # with fewer attempts keep their last score, so the line spans every
        # attempt made anywhere in the document and is directly comparable to
        # any concept's trend on the same x-axis (matched by attempt number).
        #
        # NOT capped like the per-concept series: the frontend aligns by
        # absolute attempt number, and a trailing-window cap here would drop
        # the overlay for concepts whose attempts predate the window. Bounded
        # by the user's attempts in this document — two small fields per point.
        max_attempts = max(len(t) for t in full_trajectories.values())
        n_concepts = len(full_trajectories)
        doc_avg_points = [
            {
                'attempt_number': i + 1,
                'score': round(
                    sum(t[i] if i < len(t) else t[-1] for t in full_trajectories.values())
                    / n_concepts,
                    1,
                ),
            }
            for i in range(max_attempts)
        ]

        concepts_out = []
        for cid, rows in by_concept.items():
            chunk = children.get(cid)
            parent = parents.get(chunk.parent_chunk_id) if chunk and chunk.parent_chunk_id else None
            doc = docs.get(rows[0].document_id)

            traj = full_trajectories[cid]
            points = [
                {
                    'attempt_number': i + 1,
                    'score': traj[i],
                    'is_correct': a.is_correct,
                    'difficulty': a.difficulty_shown,
                    'date': a.created_at.isoformat() if getattr(a, 'created_at', None) else None,
                }
                for i, a in enumerate(rows)
            ]

            # Cap the output series to the most recent attempts; attempt_number
            # keeps the TRUE attempt count so chart labels stay honest.
            if len(points) > MAX_HISTORY_POINTS:
                points = points[-MAX_HISTORY_POINTS:]

            concepts_out.append({
                'child_chunk_id': cid,
                'section_title': (
                    parent.title if parent
                    else f"Page {chunk.page_no}" if chunk and chunk.page_no
                    else f'Concept #{cid}'
                ),
                'document_id': rows[0].document_id,
                'document_title': doc.title if doc else f'Document #{rows[0].document_id}',
                'page_no': chunk.page_no if chunk else None,
                'current_score': points[-1]['score'] if points else 0.0,
                'attempts': len(rows),
                'points': points,
            })

        concepts_out.sort(key=lambda c: c['current_score'])
        return {
            'user_id': user_id,
            'document_id': document_id,
            'document_average': {'attempts': max_attempts, 'points': doc_avg_points},
            'concepts': concepts_out,
        }

    # ── LLM study plan (cached) ─────────────────────────────────────────────

    def _study_plan(self, user_id: int, weaknesses: list[dict], document_id: int | None = None) -> list[dict]:
        """Study plan per user, cached separately per document scope so a
        document-scoped profile never shows another document's plan."""
        from app.services.response_cache import get_cache

        cache = get_cache()
        scope = document_id or 'all'
        key = f'resp:agents:{user_id}:study-plan:{scope}'
        cached = cache.get(key)
        if cached is not None:
            return cached

        plan = self._build_study_plan(user_id, weaknesses)
        try:
            cache.set(key, plan, STUDY_PLAN_CACHE_TTL)
        except Exception:  # noqa: BLE001 — cache must never break the profile
            pass
        return plan

    def _build_study_plan(self, user_id: int, weaknesses: list[dict]) -> list[dict]:
        """LLM plan first, deterministic fallback when the LLM is unavailable."""
        llm_plan = self._llm_study_plan(user_id, weaknesses)
        if llm_plan:
            return llm_plan
        if weaknesses:
            return [
                {
                    'priority': i + 1,
                    'document_id': w['document_id'],
                    'document_title': w['document_title'],
                    'section_title': w['section_title'],
                    'page_no': w['page_no'],
                    'concept_chunk_id': w['child_chunk_id'],
                    'score': w['score'],
                    'severity': w['severity'],
                    'action': 'Re-read the section, then answer questions until the score clears 65.',
                    'insight': w.get('insight'),
                }
                for i, w in enumerate(sorted(weaknesses, key=lambda x: x['score'])[:8])
            ]
        return [{'priority': 1, 'action': 'No weak concepts — maintain mastery with review questions.'}]

    def _llm_study_plan(self, user_id: int, weaknesses: list[dict]) -> list[dict] | None:
        if not weaknesses:
            return None
        top = sorted(weaknesses, key=lambda x: x['score'])[:8]
        lines = '\n'.join(
            f"- {w['document_title']} / {w['section_title']} (page {w['page_no']}, score {w['score']:.0f}, severity {w['severity']})"
            for w in top
        )
        prompt = f"""You are a learning coach. A trainee has these weak concepts (lowest mastery first):

{lines}

Produce a prioritized study plan: for each concept, ONE specific, actionable step the trainee can take right now to improve (re-read which section, practice what, what to focus on). Order by impact.

Return ONLY a JSON array:
[
  {{
    "priority": 1,
    "document_id": <int or null>,
    "section_title": "<section>",
    "action": "<one concrete action>"
  }}
]"""

        data = self.llm().generate_json(prompt, user_id, 'agent_study_plan')
        if isinstance(data, list):
            return data[:8]
        return None

    @staticmethod
    def _empty_profile(user_id: int, document_id: int | None) -> dict:
        return {
            'user_id': user_id,
            'document_id': document_id,
            'summary': {
                'total_concepts': 0, 'mastered': 0, 'proficient': 0,
                'learning': 0, 'novice': 0, 'weak_concepts': 0,
                'critical_concepts': 0, 'avg_score': 0.0,
            },
            'documents': [], 'topics': [], 'weaknesses': [],
            'strengths': [],
            'study_plan': [{'priority': 1, 'action': 'No concepts tracked yet — start learning to build your capability profile.'}],
        }
