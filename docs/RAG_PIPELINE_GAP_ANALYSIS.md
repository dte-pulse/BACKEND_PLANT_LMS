# RAG Pipeline — Gap Analysis

**Scope:** Document ingestion, chunking, embedding, hybrid retrieval, Q&A synthesis, semantic cache, mind-map generation, and MCQ/adaptive-question generation.

**Audience:** Backend engineers / ML engineers on the Plant-LMS platform.

**Method:** Static code review of the full pipeline (`app/services`, `app/clients`, `app/repositories`, `app/models`, `app/api/v1/endpoints`, `app/tasks`, `alembic`), plus verification against the current test suite. Severity legend: 🔴 **High** (produces wrong answers / data corruption / blocks correctness), 🟠 **Medium** (inconsistent behavior, dead feature, degraded quality), 🟡 **Low** (hygiene, perf, observability).

---

## 1. Executive Summary

The pipeline is architecturally solid — parent/child chunking, hybrid BM25 + pgvector retrieval with RRF, semantic caching, and LLM synthesis are all in place, and several recent hardening fixes (G-2…G-19) are visible. However, the analysis found **5 high-severity correctness gaps, 15+ medium-severity consistency/design gaps, and a near-total absence of tests** for the pipeline itself. The three most impactful issues:

1. **🔴 Hash-embedding fallback silently poisons the vector store.** When the Gemini API fails (or no key is configured), `embed_text()` stores deterministic SHA-256 "embeddings" that are *cosine-incompatible* with real Gemini vectors. Mixed real/hash vectors in the same column make retrieval meaningless, and the "model version guard" (`get_model_version()`) is dead code — nothing validates vector compatibility before search.
2. **🔴 `[Preceding Section: …]` recap text is embedded into parents *and* children.** The recap injected for RAG continuity is part of the content that gets embedded, hashed, stored in learning cards/MCQs, and returned as retrieval context. This pollutes semantic similarity, creates false version-diff positives (content_hash includes the recap), and lets answers leak previous-section content.
3. **🔴 Section pass/fail collapses to a single binary answer.** `is_parent_passed()` returns `True` if the *last* attempt on the parent is correct (ordered by a per-child `attempt_number`, which is not chronological). One correct answer passes every child of a section; one wrong answer resets the whole section to 0%. No mastery criteria (e.g., N consecutive correct) exist, and "adaptive" difficulty is now hard-coded to medium (dead `DIFFICULTY_MAP`).

Additionally: two parallel Q&A paths (`/qa` vs `/learning/session/qa`) diverge on document-version resolution and cache keys; the cross-encoder reranker (G-16) and multi-doc retrieval (G-10) are implemented but never called; the retrieval threshold (0.35) was recalibrated for a blended score without evaluation data; and only `tests/test_json_parser.py` exists — the `scripts/verify_ingestion_pipeline.sh` references files that don't exist.

---

## 2. Pipeline Map

```
Upload (ingestion.py) ──► Celery process_document (document_tasks.py)
  └─ IngestionService.process_document
       Phase 1  Extract (ExtractionService) → Chunk (ChunkingService)      [extraction_service.py, chunking_service.py]
       Phase 2  Embed parents+children, learning cards, summaries           [embedding_client.py, llm_client.py]
       Phase 3  Per-section MCQs, document summary, mind-map tree           [ingestion_service.py]
                          │
                          ▼
Serving layer
  ├─ Q&A        /qa  (QaService → RagService + SemanticCacheService)
  ├─ Q&A        /learning/session/qa  (RagService + SemanticCacheService)  ← duplicate path
  ├─ Learning   /learning/session/*  (LearningSessionService + AdaptiveMcqService)
  ├─ Mind map   /learning/session/document/{id}/mindmap (+ /regenerate)
  └─ MCQ exams  /mcq/document/{id}/final-assessment, /effectiveness-exam
```

---

## 3. Findings

### A. Ingestion & Extraction

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| I-1 | 🟠 | **Fallback MCQs are not content-grounded.** If Gemini returns anything other than exactly 3 valid items, `_fallback_section_mcqs` emits generic compliance questions ("Modifications may be made at operator discretion…") that are unrelated to the section, with **fixed correct answers (C, A, D)** — a learnable pattern and no real assessment value. | `ingestion_service.py` `_generate_section_mcqs`, `_fallback_section_mcqs` |
| I-2 | 🟠 | **MCQs are only generated per parent section (3 each) and stored only under the section's *first* child.** All siblings reuse the same 3 questions. Learners effectively see ≤3 questions per section (then repeats). Legacy session flow (`get_mcq_for_chunk`) finds nothing for non-first children → triggers on-demand Gemini generation at request time (latency + cost). | `ingestion_service.py` Phase 3; `learning_session_service.py` `get_mcq_for_chunk` |
| I-3 | 🟡 | **No validation of generated MCQs:** no duplicate-question check, no check that `correct_option` ∈ {A,B,C,D}, no check that options are all distinct. A malformed row from the model is persisted as-is. | `ingestion_service.py` Phase 3 |
| I-4 | 🟠 | **OCR only fires on *completely empty* pages.** Pages with garbled/partial text (common in scanned SOPs with stamping/skew) skip OCR silently and enter the pipeline with noise. No page-level quality logging. | `extraction_service.py` `_extract_pdf` |
| I-5 | 🟡 | **DOCX "pages" are synthetic** (~250 words/page heuristic) — all DOCX citations are approximations, yet surfaced to users as real page numbers. | `extraction_service.py` `_extract_docx` |
| I-6 | 🟡 | **Section-title generation is untracked and serial.** `generate_section_title` (a) is not token-logged, and (b) runs as N sequential LLM calls during chunking for heading-less documents — a latency hotspot with no cost visibility. | `llm_client.py` `generate_section_title`; `chunking_service.py` |
| I-7 | 🟡 | **Ingestion has no partial-failure / stale-job recovery.** If the worker dies mid-Phase-2 the document stays in `embedding` status forever; Celery retries re-run the full destructive cleanup each attempt. No heartbeat/expiry. | `document_tasks.py`, `ingestion_service.py` |
| I-8 | 🟠 | **Doc-summary + changelog compare both truncate to 6000 chars** per version and concatenate — for large SOPs the changelog prompt may compare only the first 6000 chars of each, missing changes in later sections. | `llm_client.py` `compare_document_versions` |

### B. Chunking

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| C-1 | 🔴 | **The `[Preceding Section: …]` recap is part of *both* parent and child content.** Children are split from `content_with_context`, so: (a) every first child of a section embeds the *previous* section's recap; (b) `content_hash` includes the recap → unchanged sections hash as "changed" and are re-embedded/re-MCQ'd on versioning; (c) retrieval context and RAG answers can contain previous-section text; (d) mind-map stripping (`_strip_preceding_context`) exists but retrieval/QA does **not** strip it. | `chunking_service.py` `split_pages`; `rag_service.py` `generate_answer` |
| C-2 | 🟠 | **Token accounting is inconsistent across the pipeline:** chunking uses `words × 1.3`; `rag_service._estimate_tokens` and `llm_client._log_tokens` use `chars / 4`; stored `token_count` ≠ LLM context accounting. Cost reports and chunk-size budgeting are therefore skewed. No real tokenizer (tiktoken) is used anywhere. | `chunking_service.count_tokens`, `rag_service._estimate_tokens`, `llm_client._log_tokens` |
| C-3 | 🟡 | **G-12 merge leaves `char_end` stale** when a tiny section is absorbed into the previous one (`prev['char_end']` is not updated). Pass-2 sub-splitting then operates on a wrong char window → risk of overlapping/duplicated content in the final parent sections. | `chunking_service.py` merge loop |
| C-4 | 🟡 | **Page attribution for the first child of each section is shifted** because the recap prepended to the parent offsets the cumulative token→page lookup, so `page_no` on that child can point one page earlier than the real content. | `chunking_service.py` `split_pages` |
| C-5 | 🟡 | `max_chunk_tokens = 8000` hard ceiling force-splits paragraphs mid-word/sentence for very long tables (the table-protection branch is bypassed by the ceiling check). | `chunking_service.py` `_split_into_children` |

### C. Embeddings & Vector Store

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| E-1 | 🔴 | **Silent hash-embedding fallback poisons the vector store.** Any Gemini failure (rate limit, quota, transient) silently stores deterministic SHA-256 vectors that are *cosine-incompatible* with Gemini vectors. A doc re-ingested during an API outage has garbage vectors with no error flag, no model-version tag, and no retry. In no-key/dev environments *every* vector is a hash vector — retrieval + the 0.35 threshold then behave like random ranking, and the semantic cache degrades to exact-match. | `embedding_client.py` `embed_text` |
| E-2 | 🔴 | **Embedding model-version guard is dead code.** `get_model_version()` is never called; there is no column/table storing the embedding version per document; nothing validates vectors before cosine search. The alembic comment promises this guard but it was never wired. | `embedding_client.py`; `rag_service.py` |
| E-3 | 🟡 | **`Vector(768)` and `EMBEDDING_DIM` are hard-coded in multiple places** (models, migration, rag query cast). Changing dimensionality requires touching ~5 files + a data migration; no single source of truth for the DB schema type. | `models/chunk.py`, `models/parent_chunk.py`, `rag_service.py`, migration `a1b2c3d4e5f6` |
| E-4 | 🟡 | **Python-cosine fallback loads *all* chunks of a document into memory** per query (O(n)), and the vector candidates are still limited to the vector pool when BM25 runs — no pure-lexical rescue of chunks the vector search dropped. | `rag_service.py` `retrieve_chunks_hybrid` |
| E-5 | 🟡 | **Parent embeddings are computed from only the first 8000 chars** of a parent (truncation guard) while parents can exceed 14k tokens — the embedding only represents the section prefix. | `ingestion_service.py` (embed_tasks), `embedding_client.embed_text` |

### D. Retrieval

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| R-1 | 🟠 | **Threshold semantics changed without recalibration.** `RELEVANCE_THRESHOLD = 0.35` was calibrated (per comments) against pure cosine similarity, but the returned score is now `0.6·cos + 0.4·bm25_norm`. Lexical-only hits can now cross 0.35 spuriously (false in-scope) while weak-but-relevant semantic hits may fall below. There is no golden Q/A set to re-calibrate. | `rag_service.py` (`RELEVANCE_THRESHOLD`, `retrieve_chunks`) |
| R-2 | 🟠 | **RRF ranks the same candidate pool twice.** BM25 runs only over the chunks the vector search already selected (`chunks` from `vector_results`, limit `top_k*10`), so RRF cannot rescue lexically-relevant chunks the vector pass dropped — a core promise of hybrid search is not delivered. A true hybrid should run BM25 over the full document corpus. | `rag_service.py` `retrieve_chunks_hybrid` |
| R-3 | 🟠 | **Cross-encoder reranker (G-16) is dead code.** `_rerank()` is implemented but never called; retrieval returns raw RRF order. Similarly `retrieve_chunks_multi_doc` (G-10) has no callers. | `rag_service.py` (confirmed via search) |
| R-4 | 🟡 | **Reranker would re-download the model per call** (no module-level singleton) if it were wired up. | `rag_service.py` `_rerank` |
| R-5 | 🟠 | **Two divergent Q&A paths.** `/qa` (QaService) resolves the latest document version, keys the cache by `doc_id:version:topic`; `/learning/session/qa` uses the raw `payload.document_id`, no version, no topic. Consequences: duplicated cache entries, stale answers served for old versions after publish, and inconsistent `source_chunks` on cache hit (empty list) between the two paths. | `qa_service.py` vs `learning_session.py` `ask_question` |
| R-6 | 🟠 | **Cache hits lose provenance.** On hit, `source_chunk_id`/`page_ref` are `None` — the structured citations that exist on a miss are gone, so the frontend cannot render sources for cached answers. | `qa_service.py`; `semantic_cache_service.py` |
| R-7 | 🟡 | **Cache threshold (0.92) and TTL (7 days) are unvalidated and unmeasured.** No hit-rate / accuracy telemetry; answers are shared across users (any user's question can hit another user's cached answer — acceptable for SOP content, but worth an explicit decision). | `semantic_cache_service.py` |
| R-8 | 🟡 | `is_query_in_scope` uses only the *best* score; a query with one strong chunk and several weak ones is judged only on the top result. | `rag_service.py` |

### E. Q&A / Answer Synthesis

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| Q-1 | 🟠 | **Context passed to Gemini is not cleaned.** `generate_answer` concatenates raw `chunk.content`, including `[Preceding Section: …]` recaps → the model can quote/answer from the *previous* section while the citation says the current chunk/page. | `rag_service.py` `generate_answer` |
| Q-2 | 🟡 | **Cost accounting uses `chars/4`** — the same inconsistency as C-2; token-usage reports and the `_estimate_cost` pricing are systematically off. | `rag_service.py` |
| Q-3 | 🟡 | **No post-generation grounding check.** Hallucination control is prompt-only; there is no verification that the answer cites retrieved chunk indexes (violations are silently returned). | `rag_service.py` |
| Q-4 | 🟡 | **`qa_scope` column is effectively unused** — only referenced in `generate_answer` for temperature tuning; the "doc_strict / cross-doc" semantics it suggests are not enforced anywhere. | `models/document.py`, `rag_service.py` |
| Q-5 | 🟡 | **On Gemini failure, `generate_answer` returns raw excerpts** labeled "Excerpts retrieved from document:" with no error signal — users may mistake it for a synthesized answer. | `rag_service.py` |

### F. Mind Map

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| M-1 | 🟠 | **LLM-concept-tree ↔ DB-node matching is fragile heuristics.** Chapter grouping relies on substring / SequenceMatcher / Jaccard matching of LLM-generated titles against `_clean_node_title()` output; mismatched sections fall into a catch-all "Supporting Material" chapter. No deterministic link is stored, so a mind-map regeneration can silently regroup everything. | `mind_map_service.py` (G-4 cascade) |
| M-2 | 🟠 | **`/mindmap/regenerate` lets any authorized user mutate the shared document tree** (global `mind_map_json`) and burn LLM tokens on demand. No role/rate guard; regenerating while another user views can change grouping mid-session. | `learning_session.py` `regenerate_mind_map` |
| M-3 | 🟠 | **Two inconsistent progress models in one response.** Parent nodes use stored `ParentChunkProgress` (binary 0/100), while child nodes are computed from per-child attempt ratios (`correct/total × 100`). The structure endpoint (below) now reports the opposite semantics for children (parent-derived 100/0) — the frontend receives different "knowledge" values depending on which endpoint it calls. | `mind_map_service.py`; `learning_session.py` `get_document_structure` |
| M-4 | 🟡 | `get_document_structure` now fabricates `attempt_count: 1` and `knowledge_score: 100.0/0.0` per child from parent state — attempt counts are no longer truthful. | `learning_session.py` `get_document_structure` (recent change) |
| M-5 | 🟡 | Mind-map regenerator truncates each parent to 1200 chars and strips the recap, but the total outline is unbounded and then re-truncated to 14000 in the client — on large docs, later sections can be silently dropped. | `learning_session.py` regenerate; `llm_client.py` |

### G. MCQ & Adaptive Questioning

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| A-1 | 🔴 | **Section pass = single correct answer; ordering is wrong.** `is_parent_passed` returns `True` iff the last attempt for the parent is correct, ordered by `attempt_number DESC` — but `attempt_number` is *per-child*, so ordering across children is not chronological (should be `id`/`created_at`). One correct answer passes the whole section (all children → 100%); one wrong answer after several corrects resets the section to 0 and clears `completed_at`. There is no mastery criterion (e.g., 2 consecutive correct, minimum questions). | `adaptive_mcq_service.py` `is_parent_passed`, `_update_parent_progress` |
| A-2 | 🟠 | **"Adaptive" difficulty is dead.** `DIFFICULTY_MAP` is all-medium and `get_difficulty_for_next_attempt` is never called (confirmed by search); the module docstring still claims easy→medium→hard escalation. | `adaptive_mcq_service.py` |
| A-3 | 🟠 | **3 cached MCQs per section, then repeats.** With the cache always non-empty (3 rows), dynamic generation effectively never fires; after all 3 are "shown", questions repeat. No bank growth, no per-attempt freshness. | `adaptive_mcq_service.py` `get_next_question` |
| A-4 | 🟡 | **N+1 progress recomputation.** Each answer submit re-queries all children + per-parent pass checks; on large documents this is a growing query count per request. | `learning_session_service.py` `submit_child_answer` |
| A-5 | 🟡 | **Legacy vs parent-child flows coexist** with separate progress models: `UserProgress.completed_chunk_ids` (legacy `/session/chunk/*/answer`) vs `ParentChunkProgress` (parent-child `/session/child/*/answer`). `mark_understood` can mark a document 100% complete with zero MCQs answered; the two completion percentages can disagree. | `learning_session.py`, `learning_session_service.py` |
| A-6 | 🟡 | `mark_understood` uses a **mutable default** `payload: dict = {}`. | `learning_session.py` |
| A-7 | 🟡 | **`submit_assessment` doesn't normalize `selected_option`** (legacy exam path) before comparing to `correct_option` — a lowercase answer is counted wrong; the adaptive path does `.upper()` and is fine. | `mcq_service.py` `submit_assessment` vs `adaptive_mcq_service.record_attempt` |
| A-8 | 🟡 | Final-assessment / effectiveness exams **randomly sample** from the doc's MCQ bank with no topic balance or difficulty mix (all rows are medium) — exam composition is not representative. | `mcq_service.py` `get_final_assessment` |

### H. Testing, Observability & Ops

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| T-1 | 🔴 | **Near-zero test coverage of the pipeline.** Only `tests/test_json_parser.py` exists (5 tests, all passing). No tests for chunking, extraction, ingestion, embeddings, hybrid retrieval, RRF/blending, thresholds, semantic cache, mind-map grouping, or adaptive scoring. The only "pipeline verification" script, `scripts/verify_ingestion_pipeline.sh`, references paths that do not exist (`app/integrations/embedding_client.py`, `app/services/document_extraction.py`, `app/services/chunking_service.py:chunk_document_text`, `tests/test_ingestion_routes.py`) — it can never pass as-is. | `tests/`, `scripts/verify_ingestion_pipeline.sh` |
| T-2 | 🟠 | **No retrieval/QA evaluation harness.** No golden Q&A dataset, no recall@k / MRR / answer-groundedness metrics. Changes like the blended score (R-1), threshold, and mind-map matching (M-1) are made blind. | — |
| T-3 | 🟠 | **No quality telemetry.** No cache hit-rate, no retrieval latency percentiles, no per-stage ingestion timing, no detection of hash-embedding drift. Token logs exist (`TokenUsageLog`) but nothing consumes them for alerts. | `semantic_cache_service.py`, `report_tasks.py` |
| T-4 | 🟡 | **DB type safety:** `chunks.topic_id` / `subject_id` are `nullable=False` in the ORM while documents allow NULL and ingestion writes `topic_id or 0` — 0 is not a valid FK row. Similarly `MCQBank.topic_id` gets 0. This works only because no FK constraint is enforced on those columns; it will break if constraints are added. | `models/chunk.py`, `ingestion_service.py`, `models/mcq.py` |

---

## 4. Cross-Cutting Themes

1. **Two of everything.** Two QA endpoints, two cache-key schemes, two progress models, two MCQ flows — behavior drifts and frontend contracts diverge (see R-5, M-3, A-5). Consolidate on one canonical path.
2. **Token math is improvised** (chars/4, words×1.3) in four places (C-2). Adopt one estimator (tiktoken `cl100k_base` or Gemini-compatible counting) for chunk sizing *and* cost logging.
3. **Everything hinges on prompt quality with no verification layer.** JSON parsing is robust (`parse_json_robustly`), but there is no schema validation of generated MCQs/mind-map trees beyond `isinstance` checks.
4. **Fail-soft everywhere without instrumentation.** Hash embeddings (E-1), generic fallback MCQs (I-1), raw-excerpt answers (Q-5) — graceful, but none of them mark the document/user as degraded, and none surface in admin reporting.

---

## 5. Prioritized Roadmap

### P0 — Correctness (do first)
1. **Make embedding failure loud, not silent.** In `embedding_client.embed_text`: raise/return a sentinel on real-embedding failure; in ingestion, mark the document `failed_embedding` (or retry with backoff) instead of persisting hash vectors. For dev/no-key mode, either (a) disable retrieval and return a clear "ingestion incomplete" state, or (b) keep hash mode but tag every row so real and hash vectors are never mixed. *(E-1)*
2. **Wire the embedding version guard.** Add `embedding_model_version` to `documents` (or a meta table), populate at ingestion, compare in `retrieve_chunks_hybrid` before executing pgvector queries; on mismatch, trigger re-embed. *(E-2)*
3. **Stop embedding the recap.** Store raw section content in a separate column (or strip the recap before embedding/hashing/learning-card generation) and keep the recap only at query time for context continuity. Recompute `content_hash` on clean content. *(C-1)*
4. **Fix section pass/fail semantics.** Order by attempt `id DESC`; require mastery (e.g., ≥2 consecutive correct, or ≥80% over last N attempts) instead of single-answer pass; store the criterion in `ParentChunkProgress`. *(A-1)*

### P1 — Consistency & quality
5. **Unify Q&A paths.** Route `/learning/session/qa` through `QaService` (version resolution + version/topic-scoped cache + provenance on hits). *(R-5, R-6)*
6. **Real hybrid retrieval.** Run BM25 over the full document corpus (or a pure-lexical top-K from Postgres FTS) and merge with vector results via RRF; only then apply the blended threshold. Re-calibrate `RELEVANCE_THRESHOLD` against a small golden set. *(R-2, R-1)*
7. **Wire the reranker.** Call `_rerank` on the RRF top-K (module-level model singleton), and add a `rerank: bool` flag per endpoint. *(R-3, R-4)*
8. **Content-grounded fallback MCQs + validation.** Either return fewer content-derived questions or reject generation; add a validator (distinct options, valid `correct_option`, no duplicate questions) before persisting; expand to ≥5 MCQs per section and distribute across children. *(I-1, I-3, I-2)*
9. **Strip recap before LLM calls in `generate_answer`** (reuse `_strip_preceding_context`). *(Q-1)*
10. **Single token estimator** shared by chunking, logging, and cost reporting. *(C-2, Q-2)*

### P2 — Hardening & testing
11. **Restore adaptive difficulty** or remove the dead code — if difficulty tiers aren't desired, delete `DIFFICULTY_MAP`/`get_difficulty_for_next_attempt` and the module docstring claims. *(A-2)*
12. **Mind-map determinism:** persist the LLM title→node mapping (stable_id links) at generation time instead of fuzzy-matching on every read; guard `/regenerate` to admin/trainer roles with a rate limit. *(M-1, M-2)*
13. **Stale-job recovery:** heartbeat `status='embedding'` rows and mark them failed after a TTL. *(I-7)*
14. **Fix `verify_ingestion_pipeline.sh`** to reference the real module paths, or replace it with a proper pytest suite. *(T-1)*
15. **Add a pipeline test suite:** chunking invariants (no recap in child hashes, token bounds, page monotonicity), extraction fixtures (PDF/DOCX/empty-page OCR), embedding-fallback behavior, RRF/blend math, semantic-cache key/version behavior, adaptive scoring edge cases, mind-map grouping stability. *(T-1, T-2)*
16. **Telemetry:** cache hit-rate, retrieval latency p50/p95, ingestion stage timings, and an alert when a document finishes with any fallback paths taken. *(T-3)*

---

## 6. Quick Wins (small diffs, immediate value)

- Strip the recap in `rag_service.generate_answer` context (`import` the existing `_strip_preceding_context` from `mind_map_service` or move it to a shared util). *(Q-1)*
- Order `is_parent_passed` by `id.desc()` instead of `attempt_number.desc()`. *(A-1)*
- Normalize `selected_option.upper()` in `mcq_service.submit_assessment`. *(A-7)*
- Add `topic_id`/`doc_version` to the `/learning/session/qa` cache key and route through the same resolution as `QaService`. *(R-5)*
- Remove the mutable default in `mark_understood`. *(A-6)*
- Persist `embedding_model_version` on `documents` at ingestion and log a warning in retrieval when it differs. *(E-2)*
- Add a `post-generation` check that the answer contains ≥1 `[Page X, Chunk Y]` citation when context was provided. *(Q-3)*

---

*Generated: Aug 2026 · Backend · Plant-LMS. Findings verified against the current codebase; severity reflects production impact assuming a configured Gemini key and pgvector database.*
