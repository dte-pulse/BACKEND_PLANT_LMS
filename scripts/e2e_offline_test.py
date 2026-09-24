"""
Offline end-to-end verification (no API credits needed).

Drives the REAL pipeline end to end for TWO Cloud-Deployment course documents:

  1. CI-CD-SOP-001   — structured DOCX: numbered headings + Word heading
                       styles  → deterministic mind map, strict thresholds
  2. CLOUD-GUIDE-001 — unstructured DOCX: plain paragraphs (no headings)
                       → semantic clustering path (P2 #5), relaxed thresholds

Everything LLM-shaped is stubbed deterministically (learning cards, contextual
headers, captions, summaries, mind map); MCQs use the pipeline's own
content-grounded fallback generator (deterministic, no API). Embeddings use
the eval suite's bag-of-words vectors (offline, deterministic, SEMANTIC — the
pipeline's dev hash embedder has no semantic signal, so retrieval/clustering
would be meaningless with it).

Verifies:
  A. Ingestion: status=ready, structure_type classification, parents/chunks,
     MCQs (5 per section), learning cards, contextual headers, E-2 version tag
  B. Mind map: real MindMapService.build_mind_map — nodes, concept tree,
     deterministic chapters for the numbered SOP, scores/progress shape
  C. Learning material: document summary, per-section learning cards, MCQ bank
  D. RAG (offline): in-scope questions retrieve the right chunks under the
     per-type thresholds; out-of-scope questions retrieve nothing
  E. Eval metrics: the golden Q/A harness (synthetic mode, CI-safe)

Run:  cd Backend && ./venv/bin/python scripts/e2e_offline_test.py
"""
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.embedding_client import HASH_EMBEDDING_VERSION  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

PASS, FAIL = '✓', '✗'
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    _results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name}" + (f" — {detail}" if detail else ''))
    return ok


def section(title: str):
    print(f"\n{'─' * 72}\n{title}\n{'─' * 72}")


# ── Offline LLM + embedding stubs ──────────────────────────────────────────────

def _stem(tok: str) -> str:
    """Crude suffix stripper so the BoW stub approximates real-embedding
    semantics ('approve'≈'approval', 'deployments'≈'deployment'). Applied
    consistently to every token, so identity is preserved — only near-forms
    gain the overlap a real model would give them."""
    for suf in ('ations', 'ation', 'ments', 'ment', 'ings', 'ing', 'ional',
                'ion', 'ally', 'ers', 'ies', 'es', 'ed', 'er', 'al', 's'):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            tok = tok[:-len(suf)]
            break
    if tok.endswith('e') and len(tok) >= 6:
        tok = tok[:-1]
    return tok


def _offline_vec(text: str) -> list[float]:
    """Deterministic, SEMANTIC offline embedding at the pgvector width (768).

    md5-bucketed bag of CONTENT words over the FULL 768-dim space: same word →
    same dimension, so keyword overlap produces real cosine signal while
    out-of-scope text stays near zero. (The eval suite's 64-dim hash_vec
    suffers md5-mod-64 collisions that leak 0.2–0.5 similarity between
    unrelated texts — enough to defeat the R-2 cosine floor on small corpora.)
    Function words and tiny tokens are dropped (same spirit as the BM25
    stopword list) — otherwise shared boilerplate dominates the cosine and
    topic boundaries disappear."""
    from app.services.rag_service import _BM25_STOPWORDS
    vec = [0.0] * 768
    for token in (text or '').lower().split():
        token = ''.join(ch for ch in token if ch.isalnum())
        if len(token) <= 3 or token in _BM25_STOPWORDS:
            continue
        token = _stem(token)
        idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % 768
        vec[idx] += 1.0
    return vec


class _StubLLM:
    """Duck-typed LLMClient replacement: same method signatures, deterministic
    outputs. `embedding_client` is provided so the P2 #5 clustering path runs."""

    # use_real=True so the P2 #5 clustering path engages (it refuses clients
    # it considers hash-mode by design); vectors are still offline+det.
    embedding_client = None  # assigned after _OfflineEmbedder is defined

    def generate_contextual_header(self, chunk_content, section_title, document_title,
                                   section_index, total_sections, user_id=0):
        return f"[Stub header] This excerpt is from '{section_title}' of {document_title}."

    def generate_table_caption(self, serialized_rows, document_title, section_title, user_id=0):
        return f"[Stub caption] Table in '{section_title}' of {document_title}."

    def generate_learning_card(self, content, user_id=0):
        clean = ' '.join((content or '').split())
        return f"[Card] {clean[:120]}"

    def generate_topic_summary(self, chunk_texts, user_id=0):
        return f"[Stub summary] Document covering {len(chunk_texts)} sections."

    def generate_section_title(self, content, user_id=0):
        return ' '.join((content or '').split()[:4]) or 'Section'

    def generate_batched_section_titles(self, excerpts, user_id=0):
        return [self.generate_section_title(e) for e in excerpts]

    def generate_mind_map_structure(self, outline_text, document_title='', user_id=0):
        """Offline tree stand-in. NOTE: the pipeline contract is a LIST of
        chapter dicts (mind_map_json is stored as a JSON array) — each chapter
        binds to parent order via section_indexes (the P2 #2 contract)."""
        n_sections = outline_text.count('Section ')
        n_chapters = min(3, max(1, n_sections))
        per = -(-n_sections // n_chapters)  # ceil-div
        chapters = []
        for i in range(n_chapters):
            idxs = list(range(i * per, min((i + 1) * per, n_sections)))
            chapters.append({
                'title': f'{document_title} — Part {i + 1}',
                'section_indexes': idxs,
                'children': [{'title': f'Topic {i + 1}.{j + 1}', 'children': []}
                             for j in range(2)],
            })
        return chapters

    def compare_document_versions(self, prev_text, new_text, user_id=0):
        return '[Stub changelog] Initial version.'


class _OfflineEmbedder:
    """Forces offline embeddings even when a key exists in .env — no credits.

    ``use_real=True`` is deliberate: the pipeline branches on this flag for
    (a) the E-2 version comparison and (b) enabling the semantic-clustering
    chunker. Both version methods still report the hash version so the stored
    ``embedding_model_version`` matches, keeping hash-mode documents searchable
    (the E-2 fix contract)."""

    use_real = True

    def embed_text(self, text):
        return _offline_vec(text or '')

    def embed_texts(self, texts):
        return [self.embed_text(t) for t in texts]

    @staticmethod
    def get_model_version():
        # Mirrors EmbeddingClient semantics: real-mode clients tag docs with
        # the model version (query-time E-2 compares against the same).
        return HASH_EMBEDDING_VERSION

    @staticmethod
    def get_hash_version():
        return HASH_EMBEDDING_VERSION


# Wire the offline embedder into the LLM stub (clustering reads it via
# llm_client.embedding_client; declared late so the class exists first).
_StubLLM.embedding_client = _OfflineEmbedder()


# ── Course fixtures: Cloud Deployment course ───────────────────────────────────

def _para(topic: str, words: int, seed: str = '') -> str:
    """A fluent paragraph about `topic`, ~`words` words, with `seed` guaranteed."""
    filler = [
        'The platform requirements emphasise consistency and traceability',
        'Operators follow the documented workflow for every change',
        'Teams review the outcome against the agreed service targets',
        'Automation keeps the process repeatable across environments',
        'The reference architecture keeps each concern isolated',
    ]
    parts, i, n = [], 0, len(filler)
    while sum(len(p.split()) for p in parts) < words:
        parts.append(filler[i % n] + f' (variant {i} {seed})' if i % 3 == 0 else filler[i % n])
        i += 1
    sentence = topic if not parts else topic + '. ' + '. '.join(parts) + '.'
    return ' '.join(sentence.split()[:words + 25])


def _pad(topic: str, seed: str, words: int = 170) -> str:
    """~170-word paragraph about `topic` whose vocabulary stays on-topic."""
    filler_bank = {
        'generic': [
            'Every change follows the documented rollout workflow',
            'The team reviews each deployment against the service targets',
            'Automation keeps the process repeatable across all environments',
            'The reference architecture keeps each concern isolated',
            'Consistent naming makes dashboards and alerts easy to navigate',
        ],
    }
    sentences = [topic]
    bank = filler_bank['generic']
    total = len(topic.split())
    i = 0
    while total < words - 20:
        s = bank[i % len(bank)]
        sentences.append(s + f' during {seed} step {i}.' if i % 2 else s + '.')
        total += len(s.split()) + 3
        i += 1
    return ' '.join(sentences)


SOP_SECTIONS = {
    '1. Purpose and Scope': _pad(
        'This SOP governs automated deployment pipelines for all plant software services. '
        'The scope covers build automation, automated testing, staging promotion, and '
        'production release activities. Manual hotfixes applied directly to production '
        'servers are prohibited under this procedure. The error rate and rollback thresholds '
        'defined here apply to every service.', seed='scope'),
    '2. Pipeline Stages': _pad(
        'The pipeline executes six mandatory stages. The build stage compiles the service and '
        'runs unit tests with a minimum coverage gate. Artifacts are signed and scanned; '
        'container images with CRITICAL vulnerabilities block promotion. Staging runs a '
        'blue-green deployment with smoke tests. Production rollout is a canary release '
        'starting at five percent of traffic.', seed='stages'),
    '3. Deployment Approval and Change Control': _pad(
        'Production deployments require Change Advisory Board approval and a linked change '
        'ticket before execution. A two-person rule applies: the deployer and the approver '
        'must be different engineers. The standard production deploy window is Tuesday and '
        'Thursday between ten and sixteen hours; release freezes supersede the window.', seed='approval'),
    '4. Rollback Procedure': _pad(
        'A rollback is triggered when the error rate exceeds two percent for five minutes or '
        'when p95 latency exceeds eight hundred milliseconds after a release. The on-call '
        'engineer executes the rollback command to return to the previous release, then '
        'verifies service health against the checklist and completes the incident record.', seed='rollback'),
    '5. Pipeline Monitoring and Alerting': _pad(
        'Pipeline health is tracked with the four DORA metrics: deployment frequency, lead '
        'time for changes, mean time to restore, and change failure rate with a target below '
        'fifteen percent. Dashboards display stage durations and failure hotspots, and alerts '
        'route to the on-call rota for every failed deployment.', seed='monitoring'),
}

_GUIDE_TOPICS = [
    # Topic sentences use DISJOINT content vocabulary so the offline bag-of-
    # words embedder (and the real one) sees crisp inter-topic boundaries.
    ('Network foundations and subnet design', 'network'),
    ('Kubernetes clusters and autoscaling', 'kubernetes'),
    ('Managed PostgreSQL and backups', 'postgres'),
    ('Secrets management and credential rotation', 'secrets'),
    ('Continuous integration pipelines', 'cicd'),
    ('Zero downtime rolling updates', 'rolling'),
    ('Blue-green deployment strategy', 'bluegreen'),
    ('Canary analysis for high traffic', 'canary'),
    ('Observability with Prometheus dashboards', 'observability'),
    ('Incident response and on-call runbook', 'incident'),
]

_GUIDE_FILLER = {
    'network': [
        'Each environment runs inside a dedicated virtual private cloud with public subnets for load balancers',
        'Private subnets host the workloads and block direct internet access',
        'Security groups deny all ingress by default and open only required ports',
    ],
    'kubernetes': [
        'Node pools autoscale between three and twelve nodes on CPU and memory pressure',
        'The horizontal pod autoscaler keeps application pods within latency targets',
        'Requests and limits are mandatory so the scheduler packs nodes efficiently',
    ],
    'postgres': [
        'PostgreSQL runs as a managed service with automated backups and point in time recovery',
        'A read replica serves reporting queries without touching the primary',
        'Connection strings are read from the secrets vault at startup',
    ],
    'secrets': [
        'All credentials and signing certificates live in the secrets vault with ninety day rotation',
        'Workloads receive short lived tokens through their identity role',
        'Long lived static keys are not used anywhere in the platform',
    ],
    'cicd': [
        'Continuous integration uses GitHub Actions for every pull request',
        'Linting, unit tests, and container image builds run on each change',
        'Merges to the main branch produce signed images for deployment',
    ],
    'rolling': [
        'Rolling updates replace pods in small batches while readiness probes gate each step',
        'User traffic never reaches a pod that is not ready',
        'A failed readiness probe pauses the rollout automatically',
    ],
    'bluegreen': [
        'The blue-green strategy keeps two identical stacks and switches traffic between them',
        'The new version receives traffic only after smoke tests pass on the idle stack',
        'The load balancer can switch back within seconds if the new version misbehaves',
    ],
    'canary': [
        'Canary analysis routes a small slice of live traffic to the candidate version',
        'Automated analysis compares error rates and latency against the baseline',
        'The rollout proceeds only while the metrics stay healthy',
    ],
    'observability': [
        'Prometheus scrapes every service and Grafana dashboards track the golden signals',
        'Every deployment is annotated on the charts so regressions correlate with releases',
        'Alerting thresholds follow the documented service level objectives',
    ],
    'incident': [
        'The first responder triages alerts and applies the documented mitigation',
        'Escalation follows the paging policy if impact continues',
        'Postmortems are blameless and produce tracked action items',
    ],
}


def _guide_paragraphs(topic, seed, total_words=165):
    """A topic block of 2–3 SHORT paragraphs (~55 words each, like real docs).
    Paragraph boundaries matter: they are both the DOCX pagination unit and the
    clustering segmenter's input — one giant paragraph per topic would defeat
    the semantic-clustering path under test."""
    filler = _GUIDE_FILLER[seed]
    paras, used, i = [], 0, 0
    while used < total_words:
        body = []
        if not paras:
            body.append(topic + '.')  # topic sentence leads the block
            w = len(topic.split())
        else:
            s = filler[i % len(filler)]
            body.append(s + '.')
            w = len(s.split())
        while w < 45:
            i += 1
            s = filler[i % len(filler)]
            body.append(s + '.')
            w += len(s.split())
        paras.append(' '.join(body))
        used += w
        i += 1
    return paras


GUIDE_PARAGRAPHS = [p for t, s in _GUIDE_TOPICS for p in _guide_paragraphs(t, s)]


def _make_docx(path: Path, title: str, numbered: dict | None = None,
               paragraphs: list[str] | None = None):
    """Build a DOCX: numbered sections with Word heading styles (structured)
    or plain body paragraphs only (unstructured).

    The unstructured variant deliberately has NO heading paragraphs at all:
    a document title line becomes an H1 markdown heading after DOCX→markdown
    conversion, and any markdown heading routes the chunker to the heading-
    split path instead of the semantic-clustering path under test."""
    from docx import Document as DocxDocument

    doc = DocxDocument()
    if numbered:
        doc.add_heading(title, level=1)
        for heading, body in numbered.items():
            doc.add_heading(heading, level=2)
            doc.add_paragraph(body)
    else:
        for para in (paragraphs or []):
            doc.add_paragraph(para)
    doc.save(str(path))


def _setup_course(db):
    """Create/refresh the Cloud Deployment subject + topic, return (subject, topic)."""
    from app.models.subject import Subject
    from app.models.topic import Topic

    subject = db.query(Subject).filter(Subject.name == 'Cloud Deployment').first()
    if not subject:
        subject = Subject(name='Cloud Deployment', department='Engineering')
        db.add(subject)
        db.flush()
    topic = db.query(Topic).filter(
        Topic.subject_id == subject.id, Topic.title == 'CI/CD & Deployment Pipelines'
    ).first()
    if not topic:
        topic = Topic(subject_id=subject.id, title='CI/CD & Deployment Pipelines', sequence_order=1)
        db.add(topic)
        db.commit()
    db.refresh(subject)
    db.refresh(topic)
    return subject, topic


def _cleanup_previous_runs(db, codes):
    """Remove documents (and derived rows + assignments) from earlier runs."""
    from app.models.document import Document
    from app.models.training import TrainingAssignment
    from app.services.ingestion_service import IngestionService

    for code in codes:
        for doc in db.query(Document).filter(Document.code == code).all():
            # Assignments point at the doc id; drop them before the doc goes
            # away so no dangling rows survive the re-ingest.
            db.query(TrainingAssignment).filter(
                TrainingAssignment.document_id == doc.id
            ).delete(synchronize_session=False)
            IngestionService(db)._cleanup_document_data(doc)
            db.delete(doc)
    db.commit()


REVIEWER_EMPLOYEE_CODE = 'TRAINEE-TEST'


def _assign_to_reviewer(db, doc_ids):
    """Assign the freshly ingested docs to the review trainee so the course
    shows up under /api/v1/learning/assigned for manual review. Idempotent."""
    from app.models.training import TrainingAssignment
    from app.models.user import User

    reviewer = db.query(User).filter(
        User.employee_code == REVIEWER_EMPLOYEE_CODE
    ).first()
    if not reviewer:
        check('assigned to reviewer', False,
              f"no user with employee_code={REVIEWER_EMPLOYEE_CODE!r}")
        return
    if reviewer.role.value != 'trainee':
        print(f"  ⚠ {REVIEWER_EMPLOYEE_CODE} has role={reviewer.role.value!r} (expected trainee)")

    created = []
    for doc_id in doc_ids:
        existing = db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id == reviewer.id,
            TrainingAssignment.document_id == doc_id,
        ).first()
        if not existing:
            db.add(TrainingAssignment(
                user_id=reviewer.id,
                document_id=doc_id,
                training_type='sop',
                status='assigned',
            ))
            created.append(doc_id)
    db.commit()
    check('assigned to reviewer', True,
          f"{len(doc_ids)} docs → {reviewer.full_name} ({reviewer.employee_code}), "
          f"{len(created)} new row(s)")


def _create_document(db, subject, topic, code, title, file_name, file_type):
    from app.models.document import Document

    doc = Document(
        code=code, title=title, topic=topic.title, topic_id=topic.id,
        subject_id=subject.id, version=1, sequence_order=1, status='draft',
        is_latest=True, file_name=file_name, file_type=file_type,
        file_url=file_name, qa_scope='doc_strict',
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ── Verifications ──────────────────────────────────────────────────────────────

def verify_ingestion(doc, result):
    section(f"A. INGESTION — {doc.code} ({doc.title})")
    check('status = ready', doc.status == 'ready', f"status={doc.status}")
    check('no failure_reason', not doc.failure_reason)

    from app.models.parent_chunk import ParentChunk
    from app.models.chunk import Chunk
    from app.db.session import SessionLocal as _S
    db = _S()
    parents = db.query(ParentChunk).filter(ParentChunk.document_id == doc.id)\
        .order_by(ParentChunk.section_index).all()
    chunks = db.query(Chunk).filter(Chunk.document_id == doc.id).all()
    db.close()

    expected_type = 'structured' if doc.code == 'CI-CD-SOP-001' else 'unstructured'
    check('parent sections created', len(parents) >= 3, f"{len(parents)} sections")
    check('child chunks created', len(chunks) >= 1,
          f"{len(chunks)} chunks ({len(chunks) / max(1, len(parents)):.1f}/section)")
    check(f"structure_type = {expected_type}", doc.structure_type == expected_type,
          f"classified={doc.structure_type}")
    check('E-2 version tag stored', doc.embedding_model_version == HASH_EMBEDDING_VERSION,
          f"version={doc.embedding_model_version}")
    cards = sum(1 for c in chunks if c.learning_card)
    check('learning cards on every chunk', cards == len(chunks), f"{cards}/{len(chunks)}")
    headers = sum(1 for c in chunks if c.contextual_header)
    check('contextual headers stored', headers > 0, f"{headers}/{len(chunks)} chunks")
    check('MCQs = 5 per section', result.get('mcq_count') == 5 * len(parents),
          f"{result.get('mcq_count')} MCQs")
    return len(parents)


def verify_mind_map(doc, n_parents):
    section(f"B. MIND MAP — {doc.code}")
    from app.services.mind_map_service import MindMapService
    from app.db.session import SessionLocal as _S
    db = _S()
    try:
        mm = MindMapService(db, user_id=0).build_mind_map(doc.id)
    finally:
        db.close()

    check('response envelope', mm.get('document_id') == doc.id and 'nodes' in mm)
    # ``nodes`` is the CHAPTER-GROUPED view (type='chapter'); the flat parent
    # list lives in ``flat_nodes`` (falls back to ``nodes`` on the no-parents
    # synthetic path).
    flat = mm.get('flat_nodes') or mm.get('nodes', [])
    parents = [n for n in flat if n.get('type') == 'parent']
    check('one node per parent section', len(parents) == n_parents,
          f"{len(parents)}/{n_parents}")
    check('node titles = real section titles', all(p.get('title') for p in parents),
          f"e.g. '{parents[0].get('title', '')[:48]}…'" if parents else 'no parents')
    tree = mm.get('concept_tree') or []
    check('concept tree present', len(tree) > 0, f"{len(tree)} chapters")
    for ch in tree[:3]:
        subs = ', '.join(st.get('title', '') for st in ch.get('children', [])[:3])
        print(f"      • {ch.get('title')}: {subs}{'…' if len(ch.get('children', [])) > 3 else ''}")
    if doc.code == 'CI-CD-SOP-001':
        numbered_children = sum(
            1 for ch in tree for st in ch.get('children', [])
            if str(st.get('title', '')).lstrip('#').strip()[:2].rstrip('.').isdigit()
        )
        total_children = sum(len(ch.get('children', [])) for ch in tree)
        check('deterministic chapters (document-accurate)',
              total_children > 0 and numbered_children / max(1, total_children) >= 0.7,
              f"{numbered_children}/{total_children} numbered sub-topics")
    check('progress fields present',
          {'total_score', 'overall_progress_pct', 'completed_parents'} <= set(mm),
          f"progress={mm.get('overall_progress_pct')}% (no user progress yet — expected 0)")


def verify_learning_material(doc):
    section(f"C. LEARNING MATERIAL — {doc.code}")
    from app.models.mcq import MCQBank
    from app.models.chunk import Chunk
    from app.db.session import SessionLocal as _S
    db = _S()
    try:
        summary = doc.summary or ''
        check('document summary generated', len(summary) > 20, summary[:70] + '…')
        mcqs = db.query(MCQBank).filter(MCQBank.document_id == doc.id).all()
        check('MCQ bank populated', len(mcqs) >= 5, f"{len(mcqs)} questions")
        ok_shape = all(
            isinstance(m.options, dict) and len(m.options) == 4
            and m.correct_option in m.options
            for m in mcqs
        )
        check('MCQs well-formed (4 options, valid key)', ok_shape)
        sample = db.query(Chunk).filter(
            Chunk.document_id == doc.id, Chunk.learning_card.isnot(None)
        ).first()
        check('sample learning card', sample is not None,
              (sample.learning_card[:70] + '…') if sample else 'none')
        from app.models.document import Document as DocModel
        fresh = db.query(DocModel).filter(DocModel.id == doc.id).first()
        check('mind_map_json persisted on document', len(fresh.mind_map_json or []) > 0,
              f"{len(fresh.mind_map_json or [])} chapters")
    finally:
        db.close()


RAG_QUESTIONS = {
    'CI-CD-SOP-001': [
        ('What triggers a rollback of a production release?', ['rollback'], True),
        ('How many DORA metrics are tracked for pipeline health?', ['deployment frequency'], True),
        ('Who must approve production deployments?', ['Change Advisory Board'], True),
        ('What is the recipe for chocolate cake?', [], False),
    ],
    'CLOUD-GUIDE-001': [
        ('How are zero downtime deployments achieved?', ['rolling updates'], True),
        ('Where are database credentials stored?', ['secrets vault'], True),
        ('What is the recipe for chocolate cake?', [], False),
    ],
}


def verify_rag(doc):
    section(f"D. RAG (offline vectors, per-type thresholds) — {doc.code}")
    from app.services.rag_service import RagService
    from app.services import rag_service as rs
    from app.db.session import SessionLocal as _S

    rs._BM25_CACHE.pop(doc.id, None)  # fresh index for this doc's corpus
    db = _S()
    try:
        rag = RagService(db, user_id=0)
        rag.embedding_client = _OfflineEmbedder()  # use_real=True → E-2 compares model versions

        rel_thr = {'structured': 0.35, 'unstructured': 0.25}.get(doc.structure_type, 0.35)
        for question, keywords, in_scope in RAG_QUESTIONS[doc.code]:
            try:
                scored = rag.retrieve_chunks_scored(doc.id, question, top_k=3)
            except Exception as e:
                check(f"'{question[:44]}…'", False, f'retrieval error: {e}')
                continue
            gated = [(s, c) for s, c in scored if s >= rel_thr]
            top = gated[0] if gated else None
            if in_scope:
                content_l = (top[1].content or '').lower() if top else ''
                hit = top is not None and all(k.lower() in content_l for k in keywords)
                check(f"in-scope: '{question[:46]}…'", hit,
                      (f"score={top[0]:.3f}" if top else 'no chunk retrieved')
                      + ('' if hit else ' — keyword miss in top chunk'))
            else:
                check(f"out-of-scope rejected: '{question[:34]}…'", not gated,
                      'no chunks above threshold' if not gated else
                      f"LEAKED at {gated[0][0]:.3f}")
    finally:
        db.close()


def verify_evals():
    section('E. EVAL METRICS — golden Q/A retrieval harness (synthetic, CI-safe)')
    from app.evals.retrieval_eval import run_synthetic
    report = run_synthetic()
    check('golden Q/A harness green', report['accuracy'] >= 0.85,
          f"{report['passed']}/{report['total']} questions (accuracy={report['accuracy']:.0%})")
    if report['failures']:
        for q, why in report['failures']:
            print(f"      x {q}: {why}")


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    t0 = time.monotonic()
    print('╔' + '═' * 70 + '╗')
    print('║  OFFLINE E2E — Cloud Deployment course (no API credits needed)        ║')
    print('╚' + '═' * 70 + '╝')

    from app.core.config import settings
    if settings.gemini_api_key and settings.gemini_api_key not in ('change-me', 'replace-me'):
        print('\n⚠  A Gemini API key IS configured — the script FORCES offline stubs anyway')
        print('   (no credits burned; embeddings use the deterministic offline vectors).')
    # Force MCQ generation down the deterministic fallback path: it checks
    # settings.gemini_api_key DIRECTLY (bypassing our LLM stub), and with a
    # real key present it would call Gemini and burn ~50s on 402 errors.
    settings.gemini_api_key = 'change-me'

    from app.services.ingestion_service import IngestionService
    from app.db.session import SessionLocal as _S

    db = _S()
    try:
        subject, topic = _setup_course(db)
        print(f"\nCourse: subject='{subject.name}' topic='{topic.title}'")
        codes = ['CI-CD-SOP-001', 'CLOUD-GUIDE-001']
        _cleanup_previous_runs(db, codes)

        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        docs_spec = [
            ('CI-CD-SOP-001', 'CI/CD Pipeline Deployment SOP',
             'e2e_cicd_sop.docx', dict(numbered=SOP_SECTIONS)),
            ('CLOUD-GUIDE-001', 'Cloud Deployment Guide',
             'e2e_cloud_guide.docx', dict(paragraphs=GUIDE_PARAGRAPHS)),
        ]

        results = {}
        for code, title, fname, kw in docs_spec:
            path = upload_dir / fname
            _make_docx(path, title, **kw)
            doc = _create_document(db, subject, topic, code, title, fname, 'docx')
            print(f"\n▶ Ingesting {code} …")
            t = time.monotonic()
            svc = IngestionService(db)
            svc.llm_client = _StubLLM()          # offline LLM
            svc.embedding_client = _OfflineEmbedder()  # offline vectors (no credits)
            result = svc.process_document(doc.id)
            db.refresh(doc)
            print(f"  done in {time.monotonic() - t:.1f}s — {result['parent_count']} sections, "
                  f"{result['chunk_count']} chunks, {result['mcq_count']} MCQs")
            results[code] = doc.id

        print('\n' + '█' * 72)

        from app.models.document import Document as DocModel
        for code, doc_id in results.items():
            doc = db.query(DocModel).filter(DocModel.id == doc_id).first()
            n = verify_ingestion(doc, _mcq_count_result(db, doc_id))
            verify_mind_map(doc, n)
            verify_learning_material(doc)
            verify_rag(doc)
        verify_evals()
        _assign_to_reviewer(db, list(results.values()))
    finally:
        db.close()

    section('SUMMARY')
    failed = [(n, d) for n, ok, d in _results if not ok]
    for n, ok, d in _results:
        print(f"  {PASS if ok else FAIL} {n}")
    print(f"\n{'█' * 72}")
    if failed:
        print(f"RESULT: {len(_results) - len(failed)}/{len(_results)} checks passed — "
              f"{len(failed)} FAILED")
        raise SystemExit(1)
    print(f"RESULT: {len(_results)}/{len(_results)} checks passed "
          f"in {time.monotonic() - t0:.1f}s — pipeline verified offline ✓")


def _mcq_count_result(db, doc_id):
    from app.models.parent_chunk import ParentChunk
    n_parents = db.query(ParentChunk).filter(ParentChunk.document_id == doc_id).count()
    from app.models.mcq import MCQBank as M
    n_mcq = db.query(M).filter(M.document_id == doc_id).count()
    return {'mcq_count': n_mcq, 'expected_mcq': 5 * n_parents}


if __name__ == '__main__':
    main()
