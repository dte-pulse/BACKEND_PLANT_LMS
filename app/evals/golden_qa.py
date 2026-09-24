"""
Golden Q/A evaluation dataset (P2 #9).

Purpose: put RELEVANCE_THRESHOLD / MIN_COSINE_FLOOR calibration and retrieval
quality on a repeatable footing instead of eyeballing chat answers.

Format: one entry per curated question.
    question          — the trainee-style question
    doc_code          — document code the question targets (e.g. 'SOP-001');
                        must exist in the documents table for DB mode
    expected_keywords — terms that MUST appear in the retrieved chunks
    expected_pages    — optional page hints; when non-empty, at least one
                        retrieved chunk must come from one of these pages
    structure_type    — 'structured' | 'unstructured' (default 'structured'):
                        which #6 threshold branch (RELEVANCE_THRESHOLD_BY_TYPE /
                        MIN_COSINE_FLOOR_BY_TYPE) the entry exercises during
                        --calibrate sweeps. Must match the real document's
                        ingest-time classification (documents.structure_type).

The seed entries below are placeholders drawn from pharma SOP themes — replace
them with questions curated against YOUR real documents (aim for 50+ spread
across doc types). In-scope questions come from the docs; out_of_scope entries
must be answerable in general but NOT covered by the target document, and are
expected to retrieve nothing above threshold.

Run:
    pytest tests/test_retrieval_eval.py -q              # synthetic (no DB/API)
    python -m app.evals.retrieval_eval --real           # real docs pass/fail
    python -m app.evals.retrieval_eval --calibrate      # threshold sweep (synthetic)
    python -m app.evals.retrieval_eval --calibrate --real   # real calibration
"""
from dataclasses import dataclass, field


@dataclass
class GoldenQA:
    question: str
    doc_code: str
    expected_keywords: list[str] = field(default_factory=list)
    expected_pages: list[int] = field(default_factory=list)
    # Out-of-scope entries assert retrieval returns NOTHING above threshold.
    out_of_scope: bool = False
    # Which #6 per-type threshold branch this entry exercises during
    # calibration (defaults to 'structured').
    structure_type: str = 'structured'


GOLDEN_QA: list[GoldenQA] = [
    GoldenQA(
        question='What is the purpose of cleaning validation?',
        doc_code='SOP-001',
        expected_keywords=['cleaning', 'validation'],
    ),
    GoldenQA(
        question='Which rinse water specification applies to the final rinse?',
        doc_code='SOP-001',
        expected_keywords=['rinse', 'water'],
    ),
    GoldenQA(
        question='Explain each step of the line clearance procedure one by one.',
        doc_code='SOP-001',
        expected_keywords=['line', 'clearance'],
    ),
    GoldenQA(
        question='Who is responsible for batch record review?',
        doc_code='SOP-002',
        expected_keywords=['batch', 'review'],
    ),
    GoldenQA(
        question='What temperature range must the cold storage area maintain?',
        doc_code='SOP-002',
        expected_keywords=['temperature', 'storage'],
    ),
    GoldenQA(
        question='What is the maximum allowed deviation before escalation?',
        doc_code='SOP-003',
        expected_keywords=['deviation', 'escalation'],
        structure_type='unstructured',
    ),
    GoldenQA(
        question='Describe the media fill process and its acceptance criteria.',
        doc_code='SOP-003',
        expected_keywords=['media', 'fill'],
        structure_type='unstructured',
    ),
    GoldenQA(
        question='How often should the LAF unit be qualified?',
        doc_code='SOP-004',
        expected_keywords=['laf', 'qualification'],
        structure_type='unstructured',
    ),
    GoldenQA(
        question='What documentation is required after equipment cleaning?',
        doc_code='SOP-004',
        expected_keywords=['cleaning', 'record'],
        structure_type='unstructured',
    ),
    # ── Out of scope: must NOT retrieve above threshold ──────────────────────
    GoldenQA(
        question='Who won the last football world cup?',
        doc_code='SOP-001',
        out_of_scope=True,
    ),
    GoldenQA(
        question='What is the best recipe for chocolate cake?',
        doc_code='SOP-002',
        out_of_scope=True,
    ),
    GoldenQA(
        question='Explain quantum computing in detail.',
        doc_code='SOP-003',
        out_of_scope=True,
        structure_type='unstructured',
    ),
]
