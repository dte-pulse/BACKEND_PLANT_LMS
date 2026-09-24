"""
Retrieval evaluation harness (P2 #9).

Two modes:

1. Synthetic (CI-safe, no DB/API): drives the REAL `_retrieve_chunks_hybrid_impl`
   (RRF, blended score, MIN_COSINE_FLOOR / LEXICAL_RESCUE_FLOOR gates) over an
   in-memory corpus with a deterministic hash-stub embedder, then judges the
   results against the golden set. Runs on every pytest invocation
   (tests/test_retrieval_eval.py).

2. Real (manual calibration): `python -m app.evals.retrieval_eval --real` runs
   the golden questions against real documents + Gemini embeddings via RagService
   and reports pass rate + per-question misses so RELEVANCE_THRESHOLD can be
   tuned from data. Exits 1 when accuracy < --min-accuracy, so it can gate CI
   once a real curated golden set exists.

3. Threshold calibration (#9 'ongoing', R-1): `--calibrate` grid-searches
   (relevance_threshold, cosine_floor) per structure type by patching the #6
   constants dicts in place — the impl picks them up through
   _structure_thresholds, i.e. the exact production gate path — and prints the
   recommended RELEVANCE_THRESHOLD_BY_TYPE / MIN_COSINE_FLOOR_BY_TYPE values.
   Synthetic mode is a wiring check; `--calibrate --real` is the real thing.
"""
import argparse
import hashlib
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Calibration grid (P2 #9 'ongoing'): thresholds are searched on a fixed step so
# sweeps are reproducible and comparable over time. Range covers the plausible
# operating region for the blended score (0.6·cos + 0.4·bm25_norm): below ~0.10
# everything passes (no discrimination), above ~0.60 even perfect matches with
# weak lexical signal start failing.
_CAL_STEP = 0.05
_CAL_MIN, _CAL_MAX = 0.10, 0.60
_CAL_GRID = [round(_CAL_MIN + i * _CAL_STEP, 2)
             for i in range(int(round((_CAL_MAX - _CAL_MIN) / _CAL_STEP)) + 1)]


def _grid_pairs():
    """All (relevance, floor) combinations from the calibration grid."""
    return [(rel, floor)
            for rel in _CAL_GRID
            for floor in _CAL_GRID if floor <= rel]

_STUB_DIM = 64
# Tiny stopword set so function words never create spurious similarity.
_STOPWORDS = {
    'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her',
    'was', 'one', 'our', 'out', 'his', 'has', 'have', 'been', 'from', 'this',
    'that', 'with', 'what', 'when', 'where', 'who', 'whom', 'which', 'how',
    'why', 'does', 'did', 'each', 'last', 'best', 'won', 'explain', 'describe',
    'step', 'list',
}


# ── Shared evaluation core ────────────────────────────────────────────────────

def evaluate_retrieval(results, entry):
    """Judge one retrieval result against one golden entry.

    ``results``: list of (score, chunk) — production-shaped, i.e. already past
    the relevance gate. Returns (passed, reason).
    """
    if entry.out_of_scope:
        if results:
            return False, f'out-of-scope question retrieved {len(results)} chunk(s)'
        return True, 'no chunks above threshold (correct)'

    if not results:
        return False, 'no chunks retrieved'
    for score, chunk in results:
        content = (getattr(chunk, 'content', '') or '').lower()
        if all(kw.lower() in content for kw in entry.expected_keywords):
            if entry.expected_pages and getattr(chunk, 'page_no', None) not in entry.expected_pages:
                continue
            return True, f'matched at score {score:.3f}'
    return False, 'no retrieved chunk contains all expected keywords'


# ── Deterministic stub embedder (bag-of-words hashing) ───────────────────────

def hash_vec(text: str) -> list[float]:
    """Deterministic bag-of-words embedding for synthetic tests.

    Same word → same dimension, so keyword overlap produces real cosine signal
    and out-of-scope questions sit near zero.
    """
    vec = [0.0] * _STUB_DIM
    for token in text.lower().split():
        token = ''.join(ch for ch in token if ch.isalnum())
        if len(token) <= 3 or token in _STOPWORDS:
            continue
        idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % _STUB_DIM
        vec[idx] += 1.0
    return vec


def _cosine(a, b):
    from app.services.rag_service import _cosine_similarity
    return _cosine_similarity(a, b)


# ── Synthetic mode (CI) ───────────────────────────────────────────────────────

class _StubEmbedClient:
    def embed_text(self, text):
        return hash_vec(text)

    def get_model_version(self):
        return 'stub-v1'


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def options(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _StubDoc:
    """Minimal Document double carrying the #6 fields the pipeline reads."""

    def __init__(self, doc_id, code, structure_type=None, version=1):
        self.id = doc_id
        self.code = code
        self.structure_type = structure_type
        self.version = version
        self.embedding_model_version = None
        self.is_latest = True


class _FakeDB:
    """Serves Document lookups as empty (skips version resolution) and Chunk
    lookups as the corpus; the combined pgvector query raises so the pipeline
    takes its Python-cosine fallback (same trick as the regression tests).

    With ``docs`` set, Document lookups resolve to stub rows (id/code/
    structure_type) so the per-type threshold branch (_structure_thresholds)
    is exercised exactly as in production; without them, docs are None and
    the global fallback thresholds apply (pre-#6 behavior)."""

    def __init__(self, corpus, docs=None):
        self._corpus = corpus
        self._docs = {d.id: d for d in (docs or [])}

    def query(self, *models, **kwargs):
        if len(models) > 1:
            raise RuntimeError('pgvector unavailable (synthetic mode)')
        from app.models.document import Document
        from app.models.chunk import Chunk
        if models[0] is Document:
            return _DocQuery(list(self._docs.values()))
        if models[0] is Chunk:
            return _FakeQuery(self._corpus)
        return _FakeQuery([])


class _DocQuery:
    """Document query double that honours simple equality filters.

    The hybrid impl filters Documents by id / code / is_latest before
    _structure_thresholds reads the structure type, so the double must apply
    those criteria (introspecting SQLAlchemy BinaryExpression) instead of
    returning every row and letting .first() pick an arbitrary doc."""

    def __init__(self, rows):
        self._rows = list(rows)

    def options(self, *a, **k):
        return self

    def filter(self, *criteria, **kwargs):
        for crit in criteria:
            left = getattr(crit, 'left', None)
            right = getattr(crit, 'right', None)
            name = getattr(left, 'name', None)
            value = getattr(right, 'value', None)
            if name is None or value is None:
                continue
            self._rows = [r for r in self._rows if getattr(r, name, None) == value]
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


def _build_synthetic_corpus():
    """One chunk per in-scope golden entry (content = its keywords), plus a
    neutral distractor per document. Grouped by doc_code.

    Entries may carry ``structure_type`` (used by calibration to exercise the
    per-type threshold branch); docs default to the structured constants."""
    from app.evals.golden_qa import GOLDEN_QA

    corpus = defaultdict(list)
    next_cid = 1
    for i, entry in enumerate(GOLDEN_QA):
        if entry.out_of_scope:
            continue
        content = ' '.join(entry.expected_keywords) + ' procedures and requirements'
        corpus[entry.doc_code].append(_StubChunk(
            cid=next_cid, content=content,
            vec=hash_vec(content), page_no=i + 1,
        ))
        next_cid += 1
    for doc_code in list(corpus):
        content = 'General boilerplate introduction text'
        corpus[doc_code].append(_StubChunk(
            cid=next_cid, content=content, vec=hash_vec(content), page_no=99,
        ))
        next_cid += 1
    return corpus


class _StubChunk:
    def __init__(self, cid, content, vec, page_no):
        self.id = cid
        self.content = content
        self.embedding = vec
        self.page_no = page_no
        self.chunk_index = cid


def _doc_code_to_id(code: str) -> int:
    digits = ''.join(ch for ch in code if ch.isdigit())
    return int(digits) if digits else (abs(hash(code)) % 1000) + 1


def _entry_structure_type(entry):
    """Structure type of the target doc for one golden entry."""
    return getattr(entry, 'structure_type', None) or 'structured'


class _PatchedThresholds:
    """Temporarily patch the #6 per-type threshold constants IN PLACE.

    rag_service._structure_thresholds() imports the dicts at call time, so
    in-place updates are picked up by the live pipeline — the sweep measures
    the exact production gate path. Always restores on exit, exceptions
    included. ``overrides`` maps structure_type -> (relevance, floor)."""

    def __init__(self, overrides):
        from app.utils import constants
        self._constants = constants
        self._overrides = overrides

    def __enter__(self):
        self._orig_rel = dict(self._constants.RELEVANCE_THRESHOLD_BY_TYPE)
        self._orig_floor = dict(self._constants.MIN_COSINE_FLOOR_BY_TYPE)
        for s_type, (rel, floor) in self._overrides.items():
            self._constants.RELEVANCE_THRESHOLD_BY_TYPE[s_type] = rel
            self._constants.MIN_COSINE_FLOOR_BY_TYPE[s_type] = floor
        return self

    def __exit__(self, exc_type, exc, tb):
        self._constants.RELEVANCE_THRESHOLD_BY_TYPE.clear()
        self._constants.RELEVANCE_THRESHOLD_BY_TYPE.update(self._orig_rel)
        self._constants.MIN_COSINE_FLOOR_BY_TYPE.clear()
        self._constants.MIN_COSINE_FLOOR_BY_TYPE.update(self._orig_floor)
        return False


def _build_synthetic_docs(corpus):
    """One _StubDoc per doc_code, carrying the structure_type its golden
    entries declare, so the #6 per-type threshold branch runs in synthetic
    calibration exactly as it does against classified real documents."""
    from app.evals.golden_qa import GOLDEN_QA

    type_by_code = {}
    for entry in GOLDEN_QA:
        type_by_code.setdefault(entry.doc_code, _entry_structure_type(entry))
    return [
        _StubDoc(_doc_code_to_id(code), code, structure_type=type_by_code.get(code))
        for code in sorted(corpus)
    ]


def _synth_sweep_env() -> dict:
    """Per-doc-code RagServices over the synthetic corpus, each backed by a
    _FakeDB that resolves the doc's stub row (structure_type included)."""
    from app.services import rag_service as rs

    corpus = _build_synthetic_corpus()
    docs = _build_synthetic_docs(corpus)
    docs_by_id = {d.id: d for d in docs}
    services = {}
    for code, chunks in corpus.items():
        svc = rs.RagService.__new__(rs.RagService)
        svc.db = _FakeDB(chunks, docs=[docs_by_id[_doc_code_to_id(code)]])
        svc.user_id = 0
        svc.embedding_client = _StubEmbedClient()
        services[code] = svc
    return services


class _MemoEmbedder:
    """Query-embedding cache for the real-mode sweep: a 77-point grid must not
    re-embed every question at every point (one API call per unique query)."""

    def __init__(self, inner):
        self._inner = inner
        self._cache = {}

    def embed_text(self, text):
        if text not in self._cache:
            self._cache[text] = self._inner.embed_text(text)
        return self._cache[text]

    def get_model_version(self):
        return self._inner.get_model_version()


def run_synthetic() -> dict:
    """Run the golden set through the REAL hybrid pipeline (fallback path)."""
    from app.services.rag_service import RagService, RELEVANCE_THRESHOLD
    from app.evals.golden_qa import GOLDEN_QA

    corpus = _build_synthetic_corpus()
    services = {}
    for doc_code, chunks in corpus.items():
        svc = RagService.__new__(RagService)
        svc.db = _FakeDB(chunks)
        svc.user_id = 0
        svc.embedding_client = _StubEmbedClient()
        services[doc_code] = svc

    fails = []
    for entry in GOLDEN_QA:
        svc = services[entry.doc_code]
        doc_id = _doc_code_to_id(entry.doc_code)
        try:
            scored = svc._retrieve_chunks_hybrid_impl(doc_id, entry.question, top_k=3)
        except Exception as e:
            fails.append((entry.question, f'pipeline error: {e}'))
            continue
        # Apply the production relevance gate, then judge.
        results = [(s, c) for s, c in scored if s >= RELEVANCE_THRESHOLD]
        ok, reason = evaluate_retrieval(results, entry)
        if not ok:
            fails.append((entry.question, reason))

    total = len(GOLDEN_QA)
    accuracy = (total - len(fails)) / total if total else 0.0
    return {
        'mode': 'synthetic',
        'total': total,
        'passed': total - len(fails),
        'accuracy': round(accuracy, 4),
        'failures': fails,
    }


def run_synthetic_calibration() -> dict:
    """Grid-search (relevance_threshold, cosine_floor) per structure type.

    For each type present in the golden set, sweep every grid pair while the
    OTHER type's constants stay untouched — each optimum therefore reflects
    the production setup where both types coexist. The impl picks up the
    patched constants through _structure_thresholds (the production gate
    path); the blended relevance gate is applied at sweep level, mirroring
    retrieve_chunks().

    The synthetic corpus is built FROM the golden entries, so this mode is a
    wiring check + output-format demo; real calibration = --calibrate --real.
    """
    from app.evals.golden_qa import GOLDEN_QA

    services = _synth_sweep_env()
    s_types = sorted({_entry_structure_type(e) for e in GOLDEN_QA})
    results = {}
    for s_type in s_types:
        entries = [e for e in GOLDEN_QA if _entry_structure_type(e) == s_type]
        best = None
        for rel, floor in _grid_pairs():
            passed, total, misses = 0, 0, []
            with _PatchedThresholds({s_type: (rel, floor)}):
                for entry in entries:
                    total += 1
                    svc = services[entry.doc_code]
                    try:
                        scored = svc._retrieve_chunks_hybrid_impl(
                            _doc_code_to_id(entry.doc_code), entry.question, top_k=3)
                    except Exception as e:
                        misses.append((entry.question, f'pipeline error: {e}'))
                        continue
                    # Cosine floor was applied inside the impl via the patched
                    # constants; the blended relevance gate is ours to apply.
                    results_gated = [(s, c) for s, c in scored if s >= rel]
                    ok, reason = evaluate_retrieval(results_gated, entry)
                    if ok:
                        passed += 1
                    else:
                        misses.append((entry.question, reason))
            accuracy = passed / total if total else 0.0
            # Tie-break: higher accuracy, then the LOWEST floor and lowest rel
            # — ties resolve toward admitting more context for answer
            # synthesis, since discrimination is already equal.
            key = (accuracy, -floor, -rel)
            if best is None or key > best[0]:
                best = (key, rel, floor, accuracy, misses)
        _, rel, floor, accuracy, misses = best
        results[s_type] = {
            'best_relevance_threshold': rel,
            'best_cosine_floor': floor,
            'accuracy': round(accuracy, 4),
            'misses': misses,
        }
    return {'mode': 'synthetic', 'types': results}


def run_real_calibration() -> dict:
    """Grid-search per-type thresholds against REAL documents + embeddings.

    This is the calibration tool the #6 constants were waiting for: for each
    structure type present in the golden set, sweep the grid (other type held
    at current constants) and report the best (relevance, floor) pair per
    type. Thresholds reach the pipeline through the same in-place constants
    patch as synthetic mode; query embeddings are memoized so a 77-point grid
    costs one API call per unique question. Requires DB + embedding API access.
    """
    from app.db.session import SessionLocal
    from app.models.document import Document
    from app.services import rag_service as rs
    from app.evals.golden_qa import GOLDEN_QA

    db = SessionLocal()
    try:
        docs = {d.code: d for d in db.query(Document).all()}
        missing = sorted({e.doc_code for e in GOLDEN_QA if e.doc_code not in docs})
        if missing:
            return {'mode': 'real', 'error': f'docs not found: {missing} — curate the golden set first'}

        rag = rs.RagService(db)
        rag.embedding_client = _MemoEmbedder(rag.embedding_client)
        s_types = sorted({_entry_structure_type(e) for e in GOLDEN_QA})
        results = {}
        for s_type in s_types:
            entries = [e for e in GOLDEN_QA if _entry_structure_type(e) == s_type]
            best = None
            for rel, floor in _grid_pairs():
                passed, total, misses = 0, 0, []
                with _PatchedThresholds({s_type: (rel, floor)}):
                    for entry in entries:
                        total += 1
                        doc = docs[entry.doc_code]
                        try:
                            scored = rag.retrieve_chunks_scored(
                                doc.id, entry.question, top_k=3)
                        except Exception as e:
                            misses.append((entry.question, f'retrieval error: {e}'))
                            continue
                        # Impl applied the patched cosine floor internally; the
                        # blended relevance gate is applied here.
                        results_gated = [(s, c) for s, c in scored if s >= rel]
                        ok, reason = evaluate_retrieval(results_gated, entry)
                        if ok:
                            passed += 1
                        else:
                            misses.append((entry.question, reason))
                accuracy = passed / total if total else 0.0
                # Same tie-break as synthetic: on accuracy ties prefer the
                # most context-admitting (lowest floor, lowest rel) point.
                key = (accuracy, -floor, -rel)
                if best is None or key > best[0]:
                    best = (key, rel, floor, accuracy, misses)
            if best is None:
                continue
            _, rel, floor, accuracy, misses = best
            results[s_type] = {
                'best_relevance_threshold': rel,
                'best_cosine_floor': floor,
                'accuracy': round(accuracy, 4),
                'misses': misses,
            }
        return {'mode': 'real', 'types': results}
    finally:
        db.close()


# ── Real mode (manual calibration) ───────────────────────────────────────────

def run_real() -> dict:
    """Run the golden set against real documents via RagService."""
    from app.db.session import SessionLocal
    from app.models.document import Document
    from app.services.rag_service import RagService, RELEVANCE_THRESHOLD

    from app.evals.golden_qa import GOLDEN_QA

    db = SessionLocal()
    try:
        rag = RagService(db)
        fails = []
        missing_docs = set()
        for entry in GOLDEN_QA:
            doc = db.query(Document).filter(Document.code == entry.doc_code).first()
            if not doc:
                if entry.doc_code not in missing_docs:
                    missing_docs.add(entry.doc_code)
                fails.append((entry.question, f"doc {entry.doc_code} not found — curate the golden set against real documents"))
                continue
            try:
                scored = rag.retrieve_chunks_scored(doc.id, entry.question, top_k=3)
                results = [(s, c) for s, c in scored if s >= RELEVANCE_THRESHOLD]
            except Exception as e:
                fails.append((entry.question, f'retrieval error: {e}'))
                continue
            ok, reason = evaluate_retrieval(results, entry)
            if not ok:
                fails.append((entry.question, reason))

        total = len(GOLDEN_QA)
        accuracy = (total - len(fails)) / total if total else 0.0
        return {
            'mode': 'real',
            'total': total,
            'passed': total - len(fails),
            'accuracy': round(accuracy, 4),
            'threshold': RELEVANCE_THRESHOLD,
            'failures': fails,
        }
    finally:
        db.close()


def _print_recommendations(report):
    """Print the recommended constants update from a calibration report."""
    from app.utils import constants
    print('\n=== Recommended constants update ===')
    for s_type, r in report['types'].items():
        cur_rel = constants.RELEVANCE_THRESHOLD_BY_TYPE.get(s_type)
        cur_floor = constants.MIN_COSINE_FLOOR_BY_TYPE.get(s_type)
        rel = r['best_relevance_threshold']
        floor = r['best_cosine_floor']
        changed = '  (unchanged)' if (rel == cur_rel and floor == cur_floor) else \
                  f'  (was {cur_rel}/{cur_floor})'
        print(f"  {s_type:12s} → RELEVANCE_THRESHOLD={rel}  MIN_COSINE_FLOOR={floor}  "
              f"accuracy={r['accuracy']:.2%}{changed}")
    print("\nApply by editing RELEVANCE_THRESHOLD_BY_TYPE / MIN_COSINE_FLOOR_BY_TYPE "
          "in app/utils/constants.py.")


def main():
    parser = argparse.ArgumentParser(description='Golden Q/A retrieval evaluation')
    parser.add_argument('--min-accuracy', type=float, default=0.85,
                        help='Exit 1 when accuracy falls below this (0-1)')
    parser.add_argument('--real', action='store_true',
                        help='Run against real documents + embeddings (DB required)')
    parser.add_argument('--calibrate', action='store_true',
                        help='Grid-search per-type thresholds and recommend constants')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.calibrate:
        report = run_real_calibration() if args.real else run_synthetic_calibration()
        if 'error' in report:
            print(f"\nCalibration failed: {report['error']}")
            raise SystemExit(1)
        print(f"\n=== Threshold calibration ({report['mode']} mode) ===")
        for s_type, r in report['types'].items():
            print(f"  {s_type}: best={r['best_relevance_threshold']}" +
                  f"/floor={r['best_cosine_floor']}  accuracy={r['accuracy']:.2%}")
        _print_recommendations(report)
        if report['mode'] == 'synthetic':
            print('\nNOTE: synthetic-mode recommendations come from a trivially '
                  'separable corpus — do NOT apply them to production constants. '
                  'Run `--calibrate --real` against your curated golden set.')
        return

    report = run_real() if args.real else run_synthetic()

    print(f"\n=== Golden Q/A retrieval eval ({report['mode']} mode) ===")
    print(f"Passed: {report['passed']}/{report['total']}  (accuracy={report['accuracy']:.2%})")
    if 'threshold' in report:
        print(f"RELEVANCE_THRESHOLD: {report['threshold']}")
    if report['failures']:
        print('\nFailures:')
        for q, reason in report['failures']:
            print(f"  x {q} — {reason}")
    if report['accuracy'] < args.min_accuracy:
        print(f"\nBELOW GATE ({args.min_accuracy:.0%}) — tune thresholds or fix retrieval.")
        raise SystemExit(1)
    print('\nOK')


if __name__ == '__main__':
    main()
