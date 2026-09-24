import logging
from sqlalchemy.orm import Session

from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.mcq import MCQBank
from app.models.parent_chunk import ParentChunk
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.mcq_repository import MCQRepository
from app.services.chunking_service import ChunkingService
from app.services.extraction_service import ExtractionService
from app.utils.table_utils import (
    build_table_embed_text,
    build_table_lexical_text,
    is_table_chunk,
    parse_table,
    serialize_table,
)
from app.utils.text_utils import representative_sample, strip_preceding_context
from app.utils.structure_type import classify_structure_type

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, db: Session):
        self.db = db
        self.document_repository = DocumentRepository(db)
        self.chunk_repository = ChunkRepository(db)
        self.mcq_repository = MCQRepository(db)
        self.extraction_service = ExtractionService()
        self.chunking_service = ChunkingService()
        self.embedding_client = EmbeddingClient()
        self.llm_client = LLMClient()

    def process_document(self, document_id: int):
        document = self.document_repository.get_by_id(document_id)
        if not document:
            raise ValueError('Document not found')

        # Langfuse root trace for the whole ingestion (sampled — bulk op).
        from app.clients.langfuse_client import langfuse_observation
        from app.core.config import settings
        with langfuse_observation(
            name='ingest-document',
            as_type='span',
            tags=['ingest'],
            metadata={
                'document_id': document.id,
                'code': document.code,
                'title': document.title[:200],
                'file_type': document.file_type,
                'feature': 'ingestion',
            },
            sample_rate=settings.langfuse_ingest_sample_rate,
        ):
            return self._process_document(document)

    def _process_document(self, document: Document):
        # ── Clean up old data — FK-safe deletion order ─────────────────────
        self._cleanup_document_data(document)

        # ── Invalidate Semantic Cache ───────────────────────────────────────
        from app.services.semantic_cache_service import SemanticCacheService
        try:
            cache = SemanticCacheService()
            cache.invalidate_document(document.id)
        except Exception:
            pass

        # ── Phase 1: Extract + Chunk ────────────────────────────────────────
        try:
            self.document_repository.update(document, status='chunking', failure_reason=None)
            from app.storage.file_storage import FileStorageService
            storage = FileStorageService()
            download_url = document.file_url
            if document.file_name and storage.use_s3:
                presigned = storage.get_presigned_url(document.file_name)
                if presigned:
                    download_url = presigned

            extracted = self.extraction_service.extract(download_url, document.file_type)
            pages = extracted['pages']
            toc = extracted['toc']
            chunk_result = self.chunking_service.split_pages(pages, toc=toc, llm_client=self.llm_client)

            # Validate that extraction produced usable content
            if not chunk_result.get('parents'):
                failure_msg = 'Document produced no content — it may be scanned, image-only, encrypted, or empty.'
                self.document_repository.update(document, status='failed_extraction', failure_reason=failure_msg)
                raise ValueError(failure_msg)

        except Exception as e:
            self.db.rollback()
            if document.status != 'failed_extraction':
                self.document_repository.update(document, status='failed_extraction', failure_reason=str(e))
            raise e

        # ── Phase 2: Embed + Persist Parent & Child Chunks ─────────────────
        try:
            self.document_repository.update(document, status='embedding')
            all_child_chunks = []  # will hold persisted Chunk ORM objects

            # Find previous version of the document to do section-level diff
            prev_doc = self.db.query(Document).filter(
                Document.code == document.code,
                Document.id != document.id
            ).order_by(Document.version.desc()).first()

            prev_parents_map = {}
            prev_children_map = {}
            if prev_doc:
                prev_parents = self.db.query(ParentChunk).filter(ParentChunk.document_id == prev_doc.id).all()
                for p in prev_parents:
                    if p.stable_id:
                        prev_parents_map[p.stable_id] = p
                
                prev_children = self.db.query(Chunk).filter(Chunk.document_id == prev_doc.id).all()
                for c in prev_children:
                    if c.stable_id:
                        prev_children_map[c.stable_id] = c

            import hashlib
            import re

            def sanitize_id_part(text: str) -> str:
                if not text:
                    return ""
                text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
                text = re.sub(r'[\s-]+', '_', text)
                return text.strip('_').lower()

            def compute_hash(text: str) -> str:
                return hashlib.sha256(text.encode('utf-8')).hexdigest()

            # Pre-calculate stable_id and content_hash for all parents and children in chunk_result.
            # C-1 fix: hashes are computed on CLEAN content so unchanged sections
            # never hash as 'changed' on versioning.
            for parent_data in chunk_result.get('parents', []):
                p_title_sanitized = sanitize_id_part(parent_data['title'])
                if not p_title_sanitized:
                    p_title_sanitized = f"sec_{parent_data['section_index']}"
                parent_stable_id = f"{document.code}__{p_title_sanitized}"
                parent_data['stable_id'] = parent_stable_id
                parent_data['content_hash'] = compute_hash(strip_preceding_context(parent_data['content']))

                for child_data in parent_data.get('children', []):
                    child_stable_id = f"{parent_stable_id}__c_{child_data['child_index']:02d}"
                    child_data['stable_id'] = child_stable_id
                    child_data['content_hash'] = compute_hash(strip_preceding_context(child_data['content']))

            # G-18: Pre-compute ALL embeddings, learning cards, and parent summaries
            # in parallel using a thread pool to eliminate sequential blocking HTTP calls.
            from concurrent.futures import ThreadPoolExecutor, as_completed

            embed_tasks: list[tuple[str, str]] = []  # (key, text)
            lc_tasks: list[tuple[str, str]] = []     # (key, text) for learning cards
            summary_tasks: list[tuple[str, str]] = [] # (key, text) for parent summaries
            # Upgrade 2 — Contextual Retrieval: one LLM call per NEW child chunk
            # writes a situating header that is prepended at embed/BM25/answer
            # time. Best-effort: failures leave the header empty (pipeline runs
            # exactly as before).
            ctx_tasks: list[tuple[str, tuple]] = []  # (key, (chunk, title, doc, idx, total))
            # P2 #3 — Table Retrieval: caption tasks for table chunks (one LLM
            # call per new table chunk) + per-table metadata for embed/reuse.
            caption_tasks: list[tuple[str, tuple]] = []  # (key, (serialized, doc_title, section_title))
            table_meta: dict[str, dict] = {}             # stable_id -> {serialized, caption}

            # E-5 fix: parent embeddings use a representative beginning/middle/end
            # sample instead of only the first 8000 chars, so long sections are
            # not represented by their prefix alone.
            for parent_data in chunk_result.get('parents', []):
                p_stable_id = parent_data['stable_id']
                prev_parent = prev_parents_map.get(p_stable_id)
                p_clean = strip_preceding_context(parent_data['content'])
                is_unchanged = prev_parent and prev_parent.content_hash == parent_data['content_hash']
                if not is_unchanged:
                    embed_tasks.append((f'p::{p_stable_id}', representative_sample(p_clean, 8000)))
                    # G-5: generate real summary for new/changed parent sections
                    summary_tasks.append((f'ps::{p_stable_id}', representative_sample(p_clean, 2000)))
                n_parents = len(chunk_result.get('parents', []))
                for child_data in parent_data.get('children', []):
                    c_stable_id = child_data['stable_id']
                    prev_child = prev_children_map.get(c_stable_id)
                    c_clean = strip_preceding_context(child_data['content'])
                    is_child_unchanged = prev_child and prev_child.content_hash == child_data['content_hash']
                    if not is_child_unchanged:
                        embed_tasks.append((f'c::{c_stable_id}', c_clean))
                        lc_tasks.append((f'lc::{c_stable_id}', c_clean))
                    ctx_tasks.append((
                        f'ctx::{c_stable_id}',
                        (c_clean, parent_data['title'], document.title,
                         parent_data['section_index'], n_parents),
                    ))
                    # P2 #3 — Table Retrieval: table chunks get a serialized-rows
                    # text and an LLM one-liner caption. Both replace the raw
                    # pipe soup as the embedding/BM25 input; the caption is
                    # stored in contextual_header so it reaches rerank + answer
                    # rendering through the existing Upgrade-2 channel.
                    if is_table_chunk(c_clean):
                        serialized = serialize_table(parse_table(c_clean))
                        if serialized:
                            caption_tasks.append((
                                f'cap::{c_stable_id}',
                                (serialized, document.title, parent_data['title']),
                            ))
                            table_meta[c_stable_id] = {'serialized': serialized}

            def _embed_one(key_text):
                key, text = key_text
                return key, self.embedding_client.embed_text(text)

            def _lc_one(key_text):
                key, text = key_text
                return key, self.llm_client.generate_learning_card(text)

            def _summary_one(key_text):
                key, text = key_text
                return key, self.llm_client.generate_topic_summary([text])

            def _ctx_one(key_tuple):
                key, args = key_tuple
                return key, self.llm_client.generate_contextual_header(*args)

            def _caption_one(key_tuple):
                key, args = key_tuple
                return key, self.llm_client.generate_table_caption(*args)

            embedding_cache: dict[str, list[float]] = {}
            lc_cache: dict[str, str] = {}
            summary_cache: dict[str, str] = {}
            ctx_cache: dict[str, str] = {}

            # Upgrade 2 ordering: contextual headers are generated FIRST (they are
            # an input to the embedding text), then embeddings, then the remaining
            # LLM tasks. Header failures are non-fatal — a chunk without a header
            # simply embeds its plain content, exactly as before this upgrade.
            with ThreadPoolExecutor(max_workers=3) as pool:
                for future in as_completed([pool.submit(_ctx_one, t) for t in ctx_tasks]):
                    try:
                        k, v = future.result()
                        ctx_cache[k] = v
                    except Exception as cf:
                        logger.warning(f"Contextual header generation failed (non-fatal): {cf}")

            # P2 #3: table captions run in the same pool as contextual headers —
            # they must complete before embeddings are computed (they are an
            # input to the table embed text). Failures are non-fatal: a table
            # chunk without a caption still gets serialized-rows embeddings.
            with ThreadPoolExecutor(max_workers=3) as pool:
                for future in as_completed([pool.submit(_caption_one, t) for t in caption_tasks]):
                    try:
                        k, v = future.result()
                        stable_id = k.split('::', 1)[1]
                        if v:
                            table_meta.setdefault(stable_id, {})['caption'] = v
                    except Exception as cf:
                        logger.warning(f"Table caption generation failed (non-fatal): {cf}")

            # When a header exists, re-point the embed task at header+content so
            # the vector represents chunk + document context (Anthropic CR).
            # P2 #3: for table chunks the embed text is caption + serialized rows
            # (falling back to header + content when no caption could be made) —
            # prose-shaped text instead of pipe soup.
            contextual_embed_tasks = []
            for key, text in embed_tasks:
                stable_id = key.split('::', 1)[1]
                header = ctx_cache.get(f'ctx::{stable_id}', '')
                tmeta = table_meta.get(stable_id)
                if tmeta:
                    table_embed = build_table_embed_text(
                        tmeta['serialized'], tmeta.get('caption')
                    )
                    if not table_embed:
                        table_embed = f"{header}\n\n{text}" if header else text
                    contextual_embed_tasks.append((key, table_embed))
                else:
                    contextual_embed_tasks.append((key, f"{header}\n\n{text}" if header else text))

            # E-1: embedding failures are LOUD — they surface EmbeddingError from
            # the worker and mark the document failed_embedding instead of
            # persisting incompatible hash vectors.
            from app.clients.embedding_client import EmbeddingError as _EmbedErr
            with ThreadPoolExecutor(max_workers=5) as pool:
                for future in as_completed([pool.submit(_embed_one, t) for t in contextual_embed_tasks]):
                    try:
                        k, v = future.result()
                        embedding_cache[k] = v
                    except _EmbedErr as ef:
                        raise
                    except Exception as ef:
                        logger.warning(f"Parallel embedding failed: {ef}")

            with ThreadPoolExecutor(max_workers=3) as pool:
                for future in as_completed(
                    [pool.submit(_lc_one, t) for t in lc_tasks] +
                    [pool.submit(_summary_one, t) for t in summary_tasks]
                ):
                    try:
                        k, v = future.result()
                        if k.startswith('lc::'):
                            lc_cache[k] = v
                        else:
                            summary_cache[k] = v
                    except Exception as lf:
                        logger.warning(f"Parallel LLM task failed: {lf}")

            for parent_data in chunk_result.get('parents', []):
                parent_stable_id = parent_data['stable_id']
                p_hash = parent_data['content_hash']

                prev_parent = prev_parents_map.get(parent_stable_id)
                is_parent_unchanged = prev_parent and prev_parent.content_hash == p_hash

                if is_parent_unchanged:
                    parent_embedding = prev_parent.embedding
                    parent_summary = prev_parent.summary
                else:
                    p_key = f'p::{parent_stable_id}'
                    if p_key in embedding_cache:
                        parent_embedding = embedding_cache[p_key]
                    else:
                        # E-1: this re-raises EmbeddingError if real embedding is down
                        parent_embedding = self.embedding_client.embed_text(
                            representative_sample(strip_preceding_context(parent_data['content']), 8000)
                        )
                    # G-5: use pre-computed summary; fallback to None only if generation failed
                    parent_summary = summary_cache.get(f'ps::{parent_stable_id}') or None

                parent_obj = ParentChunk(
                    document_id=document.id,
                    topic_id=document.topic_id or 0,
                    subject_id=document.subject_id or 0,
                    section_index=parent_data['section_index'],
                    title=parent_data['title'],
                    content=parent_data['content'],
                    summary=parent_summary,
                    embedding=parent_embedding,
                    page_start=parent_data['page_start'],
                    page_end=parent_data['page_end'],
                    token_count=parent_data['token_count'],
                    stable_id=parent_stable_id,
                    content_hash=p_hash
                )
                self.db.add(parent_obj)
                self.db.flush()  # get parent_obj.id before creating children

                # Create child chunks under this parent
                for child_data in parent_data.get('children', []):
                    child_stable_id = child_data['stable_id']
                    c_hash = child_data['content_hash']

                    prev_child = prev_children_map.get(child_stable_id)
                    is_child_unchanged = prev_child and prev_child.content_hash == c_hash

                    if is_child_unchanged:
                        child_embedding = prev_child.embedding
                        child_learning_card = prev_child.learning_card
                    else:
                        c_key = f'c::{child_stable_id}'
                        # Upgrade 2: embeddings were computed over header+content in
                        # the parallel phase above (contextual_embed_tasks). The
                        # fallback re-embed here uses the stored header too.
                        # P2 #3: table chunks re-embed from caption + serialized rows.
                        c_header = ctx_cache.get(f'ctx::{child_stable_id}', '')
                        tmeta = table_meta.get(child_stable_id)
                        if tmeta:
                            embed_text_full = build_table_embed_text(
                                tmeta['serialized'], tmeta.get('caption')
                            ) or f"{c_header}\n\n{strip_preceding_context(child_data['content'])}"
                        else:
                            embed_text_full = f"{c_header}\n\n{strip_preceding_context(child_data['content'])}" if c_header else strip_preceding_context(child_data['content'])
                        if c_key in embedding_cache:
                            child_embedding = embedding_cache[c_key]
                        else:
                            # E-1: re-raises EmbeddingError rather than storing hash vectors
                            child_embedding = self.embedding_client.embed_text(embed_text_full)
                        lc_key = f'lc::{child_stable_id}'
                        if lc_key in lc_cache:
                            child_learning_card = lc_cache[lc_key]
                        else:
                            child_learning_card = self.llm_client.generate_learning_card(
                                strip_preceding_context(child_data['content'])
                            )

                    child_obj = Chunk(
                        document_id=document.id,
                        topic_id=document.topic_id or 0,
                        subject_id=document.subject_id or 0,
                        parent_chunk_id=parent_obj.id,
                        child_index=child_data['child_index'],
                        chunk_index=child_data['chunk_index'],
                        chunk_type='child',
                        page_no=child_data['page_no'],
                        token_count=child_data['token_count'],
                        content=child_data['content'],
                        learning_card=child_learning_card,
                        embedding=child_embedding,
                        stable_id=child_stable_id,
                        content_hash=c_hash,
                        # P2 #3: table chunks store their LLM caption in
                        # contextual_header — the existing Upgrade-2 channel — so
                        # rerank (header+content pairs) and answer rendering see
                        # the caption with zero query-time changes.
                        contextual_header=(
                            table_meta[child_stable_id].get('caption')
                            or ctx_cache.get(f'ctx::{child_stable_id}')
                            or None
                        ) if child_stable_id in table_meta else (
                            ctx_cache.get(f'ctx::{child_stable_id}') or None
                        ),
                    )
                    self.db.add(child_obj)
                    all_child_chunks.append(child_obj)

            self.db.commit()
            # Refresh all children to get their IDs, then snapshot into plain
            # dicts so Phase 3 never touches expired ORM objects after commit.
            chunk_snapshots = []
            for c in all_child_chunks:
                self.db.refresh(c)
                chunk_snapshots.append({'id': c.id, 'content': c.content})

            # P2 #6: classify the document's structure once, at ingest, from
            # the SAME chunk_result that produced the sections — so retrieval
            # thresholds can follow the document type (see rag_service).
            self.document_repository.update(
                document,
                structure_type=classify_structure_type(chunk_result.get('parents', [])),
            )

            # E-2: tag the document with the embedding model version used, so
            # retrieval can refuse to search vectors created under a different
            # (or hash-fallback) embedding model. resolve_versions prefers
            # CALLABLE per-instance overrides (offline stubs / test doubles
            # that produce different vectors must be able to re-tag these); the
            # previous class-level EmbeddingClient.get_model_version() call
            # ignored them and tagged stub-embedded docs with the real-model
            # version, making them unsearchable at query time.
            from app.clients.embedding_client import EmbeddingClient as _EC
            _model_version, _hash_version = _EC.resolve_versions(self.embedding_client)
            self.document_repository.update(
                document,
                embedding_model_version=_model_version if self.embedding_client.use_real
                else _hash_version,
            )

        except Exception as e:
            self.db.rollback()
            self.document_repository.update(document, status='failed_embedding', failure_reason=str(e))
            raise e

        # ── Phase 3: MCQ Generation (5 medium MCQs per parent section, spread
        # ── across children so every child has questions — I-2 fix) ────────────
        try:
            self.document_repository.update(document, status='mcq_gen')
            mcq_rows = []

            # Fetch parent chunks for this document
            parent_chunks = self.db.query(ParentChunk).filter(
                ParentChunk.document_id == document.id
            ).order_by(ParentChunk.section_index).all()

            for p_obj in parent_chunks:
                # Find children under this parent
                p_children = self.db.query(Chunk).filter(
                    Chunk.parent_chunk_id == p_obj.id
                ).order_by(Chunk.child_index).all()
                if not p_children:
                    continue

                # Check if parent is unchanged to copy MCQs
                prev_parent = prev_parents_map.get(p_obj.stable_id)
                is_parent_unchanged = prev_parent and prev_parent.content_hash == p_obj.content_hash

                copied = False
                if is_parent_unchanged:
                    # Find MCQs linked to any child of the previous parent
                    prev_child_ids = [c.id for c in self.db.query(Chunk).filter(Chunk.parent_chunk_id == prev_parent.id).all()]
                    if prev_child_ids:
                        prev_mcqs = self.db.query(MCQBank).filter(MCQBank.chunk_id.in_(prev_child_ids)).all()
                        if prev_mcqs:
                            for i, prev_mcq in enumerate(prev_mcqs):
                                # I-2: keep the same distribution — link each
                                # copied MCQ to the same child index it had
                                # (modulo children count) so every child stays
                                # covered after re-ingestion.
                                target_child = p_children[i % len(p_children)]
                                mcq_rows.append(
                                    MCQBank(
                                        document_id=document.id,
                                        topic_id=document.topic_id or 0,
                                        chunk_id=target_child.id,
                                        question=prev_mcq.question,
                                        options=prev_mcq.options,
                                        correct_option=prev_mcq.correct_option,
                                        explanation=prev_mcq.explanation,
                                        difficulty='medium',
                                        type=prev_mcq.type
                                    )
                                )
                            copied = True

                if not copied:
                    # Generate 5 medium difficulty MCQs for this parent chunk content
                    # (I-2: expanded from 3 → 5 so the section has more questions
                    # before repeats kick in). C-1: uses clean content.
                    items = self._generate_section_mcqs(strip_preceding_context(p_obj.content))
                    for i, item in enumerate(items):
                        target_child = p_children[i % len(p_children)]
                        mcq_rows.append(
                            MCQBank(
                                document_id=document.id,
                                topic_id=document.topic_id or 0,
                                chunk_id=target_child.id,  # I-2: distribute across children
                                question=item['question'],
                                options=item['options'],
                                correct_option=item['correct_option'],
                                explanation=item['explanation'],
                                difficulty='medium',
                                type='objective'
                            )
                        )

            if mcq_rows:
                self.mcq_repository.create_many(mcq_rows)

            # Generate and save document summary & NotebookLM-style mind map concept tree
            # C-1: summaries/changelog use CLEAN content (no preceding-section recap).
            summary = self.llm_client.generate_topic_summary(
                [strip_preceding_context(snap['content']) for snap in chunk_snapshots]
            )
            
            # Check versioning and perform comparison if a previous version exists
            try:
                prev_doc = self.db.query(Document).filter(
                    Document.code == document.code,
                    Document.id != document.id
                ).order_by(Document.version.desc()).first()

                if prev_doc:
                    # Retrieve the full text of the previous version from its parents
                    prev_parents = self.db.query(ParentChunk).filter(
                        ParentChunk.document_id == prev_doc.id
                    ).order_by(ParentChunk.section_index).all()
                    
                    prev_text = "\n\n".join(strip_preceding_context(p.content or '') for p in prev_parents)
                    new_text = "\n\n".join(strip_preceding_context(snap['content']) for snap in chunk_snapshots)
                    
                    if prev_text.strip():
                        changelog = self.llm_client.compare_document_versions(prev_text, new_text)
                        summary = f"{summary}\n\n### Version {document.version} Changelog (Changes from Version {prev_doc.version}):\n{changelog}"
            except Exception as comp_err:
                logger.warning(f"Failed to generate revision changelog comparison: {comp_err}")

            # G-9: Dynamic per-section char budget so later sections are never
            # silently truncated out of the concept tree on long documents.
            # P2 #2: sections are labeled "Section N:" with their ZERO-BASED
            # parent-chunk index so the LLM can bind each chapter to exact
            # sections — the mind map groups by these explicit bindings and no
            # longer fuzzy-matches the LLM's titles against chunk titles.
            n_sections = len(chunk_result.get('parents', []))
            per_section_chars = max(400, 14000 // max(1, n_sections))
            outline_text = "\n\n".join(
                f"Section {idx}: {p.get('title', '')}\n{strip_preceding_context(p.get('content', ''))[:per_section_chars]}"
                for idx, p in enumerate(chunk_result.get('parents', []))
            )
            # M-7: when the document was split by its OWN numbered X.Y headings,
            # build the concept tree DETERMINISTICALLY from the real chapter
            # headings + section titles (grouped by chapter number).  This makes
            # the mind map mirror the source PDF's chapters exactly — the LLM
            # tends to invent 4-6 semantic chapter names that don't match the
            # document.  The LLM tree is still used for non-numbered documents.
            parents = chunk_result.get('parents', [])

            def _numbered_title(t) -> bool:
                # Matches '1. Purpose' AND '1.1 Purpose' — chapter-only SOPs
                # must reach the deterministic tree too.
                return bool(re.match(r'^\d{1,2}(\.\d{1,2}){0,2}\.?\s+\S', (t or '').strip()))

            numbered_parents = [
                p for p in parents
                if p.get('chapter_num') and _numbered_title(p.get('title'))
            ]
            if len(numbered_parents) >= 2 and numbered_parents and \
                    len(numbered_parents) / len(parents) >= 0.7:
                from collections import OrderedDict
                chapters_map: OrderedDict = OrderedDict()
                for p in parents:
                    ch_num = p.get('chapter_num')
                    if not ch_num:
                        continue
                    if ch_num not in chapters_map:
                        chapters_map[ch_num] = {
                            'title': p.get('chapter_title') or f'Chapter {ch_num}',
                            'children': [],
                        }
                    chapters_map[ch_num]['children'].append({
                        'title': p.get('title'),
                        'children': [],
                    })
                mind_map_tree = list(chapters_map.values())
            else:
                mind_map_tree = self.llm_client.generate_mind_map_structure(outline_text, document.title)
            self.document_repository.update(document, status='ready', summary=summary, mind_map_json=mind_map_tree)

            # P0 #4: the new version's chunks are different — drop any cached
            # BM25 index for this document. Other processes refresh via the
            # version key change; this one is invalidated explicitly.
            try:
                from app.services.rag_service import invalidate_bm25_cache
                invalidate_bm25_cache(document.id)
            except Exception as bm25_exc:
                logger.debug(f'BM25 cache invalidation skipped: {bm25_exc}')

        except Exception as e:
            self.db.rollback()
            self.document_repository.update(document, status='failed_mcq', failure_reason=str(e))
            raise e

        return {
            'document_id': document.id,
            'status': 'ready',
            'parent_count': len(chunk_result.get('parents', [])),
            'chunk_count': len(all_child_chunks),
            'mcq_count': len(mcq_rows),
            'summary': summary,
            'failure_reason': None,
        }

    def _cleanup_document_data(self, document: Document):
        """Delete all derived data for a document in FK-safe order (child → parent).

        Re-ingestion recreates chunks/parents with NEW ids, so every row that
        references them must be removed (or its FK detached) first:

          child_chunk_attempts  → mcq_bank, chunks, parent_chunks, documents
          parent_chunk_progress → parent_chunks, documents
          user_mcq_attempts     → documents
          user_concept_mastery  → chunks, parent_chunks, documents   ← added (I-17)
          mcq_bank              → chunks, documents
          chunks                → parent_chunks, documents
          parent_chunks         → documents

        ``user_concept_mastery.child_chunk_id`` is NON-nullable, so missing this
        step raised a ForeignKeyViolation on ``DELETE FROM chunks`` — re-ingesting
        a document whose chunks were referenced by mastery rows 500'd.
        """
        from app.models.child_chunk_attempt import ChildChunkAttempt
        from app.models.parent_chunk_progress import ParentChunkProgress
        from app.models.user_concept_mastery import UserConceptMastery
        from app.models.user_mcq_attempt import UserMcqAttempt
        from app.models.user_qa_session import UserQaSession
        from app.models.user_progress import UserProgress

        # Detach foreign keys on user_qa_sessions & user_progress before deleting old chunks
        self.db.query(UserQaSession).filter(UserQaSession.document_id == document.id).update({'source_chunk_id': None})
        self.db.query(UserProgress).filter(UserProgress.document_id == document.id).update({'current_chunk_id': None})

        self.db.query(ChildChunkAttempt).filter(ChildChunkAttempt.document_id == document.id).delete()
        self.db.query(ParentChunkProgress).filter(ParentChunkProgress.document_id == document.id).delete()
        self.db.query(UserMcqAttempt).filter(UserMcqAttempt.document_id == document.id).delete()
        # user_concept_mastery references chunks.id (non-nullable) AND parent_chunks.id
        # — must be cleared before either table is pruned (I-17).
        self.db.query(UserConceptMastery).filter(UserConceptMastery.document_id == document.id).delete()
        self.db.query(MCQBank).filter(MCQBank.document_id == document.id).delete()
        self.db.query(Chunk).filter(Chunk.document_id == document.id).delete()
        self.db.query(ParentChunk).filter(ParentChunk.document_id == document.id).delete()
        self.db.commit()

    _SECTION_MCQ_COUNT = 5  # I-2: more questions per section before repeats

    def _generate_section_mcqs(self, content: str, retries: int = 1) -> list[dict]:
        """
        Generate exactly 5 multiple-choice questions of MEDIUM difficulty for a
        section, then validate them (I-3). Invalid rows are dropped; if fewer
        than 3 valid questions remain, content-grounded fallbacks fill the gap.

        Malformed LLM output is retried up to ``retries`` times before falling
        back, and ``parse_json_robustly`` salvages truncated / missing-comma
        output — so a single bad response can no longer nuke a section's MCQs
        (the previous behavior: one malformed JSON → generic fallbacks).
        """
        from app.core.config import settings
        from app.utils.json_parser import parse_json_robustly

        count = self._SECTION_MCQ_COUNT
        if not settings.gemini_api_key or settings.gemini_api_key in ('change-me', 'replace-me'):
            return self._fallback_section_mcqs(content, count)

        from app.evals.judges import judge_mcq_quality

        last_error: Exception | None = None
        for attempt in range(1, retries + 2):
            raw_text: str | None = None
            try:
                raw_text = self._generate_section_mcqs_raw(content, count)
                data = parse_json_robustly(raw_text)
            except Exception as e:  # noqa: BLE001 — retry, then fall back
                last_error = e
                logger.warning(
                    'Section MCQ generation attempt %d/%d failed: %s',
                    attempt, retries + 1, e,
                )
                if raw_text:
                    # Help debugging: surface what Gemini actually returned.
                    logger.debug('Raw LLM output (first 1000 chars): %s', raw_text[:1000])
                continue

            if isinstance(data, list):
                valid = self._validate_section_mcqs(data)
                if len(valid) >= 3:
                    # Eval: LLM-as-judge on a sampled section's MCQ batch.
                    # Best-effort: a judge failure must never fail ingestion.
                    try:
                        judge_mcq_quality(valid, content)
                    except Exception as e:  # noqa: BLE001
                        logger.warning('MCQ quality judge failed: %s', e)
                    return valid
                # Too many invalid rows — pad with content-grounded fallbacks
                return self._pad_mcqs(valid, content, count)

            # Successfully parsed but not an array (e.g. a single object):
            # retrying won't fix the shape — fall back deterministically.
            logger.warning(
                'Section MCQ generation returned unexpected shape %s (expected list)',
                type(data).__name__,
            )
            return self._fallback_section_mcqs(content, count)

        logger.error('Section MCQ generation failed after %d attempts: %s', retries + 1, last_error)
        return self._fallback_section_mcqs(content, count)

    def _generate_section_mcqs_raw(self, content: str, count: int) -> str:
        """One Gemini call returning the raw JSON text for ``count`` MCQs.

        Kept separate from parsing so the caller can retry a malformed
        response without re-observing the Langfuse generation lifecycle.
        """
        from app.core.config import settings
        from app.clients.langfuse_client import langfuse_observation
        from app.utils.tokenizer import usage_from_response

        prompt = f"""You are a subject matter expert. Generate exactly {count} multiple-choice questions based STRICTLY on the content below.
All questions must be of MEDIUM difficulty (concept application or understanding, not simple recall and not overly complex scenarios).

CONTENT:
{content[:5000]}

IMPORTANT: Base every question ONLY on the content above. Do not introduce any external domain or context.

Return ONLY a valid JSON array of {count} objects:
[
  {{"question": "<question 1>", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "<A/B/C/D>", "explanation": "<explanation 1>"}},
  ... {count - 2} more objects ...
  {{"question": "<question {count}>", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "<A/B/C/D>", "explanation": "<explanation {count}>"}}
]"""

        from google import genai as genai_sdk
        from google.genai import types
        with langfuse_observation(
            name='generate-section-mcqs',
            as_type='generation',
            tags=['ingest', 'mcq'],
            metadata={'operation': 'mcq_gen_ingest', 'section_words': len(content.split())},
            model='gemini-2.5-flash',
            input_data={'prompt': prompt[:8000]},
        ) as gen:
            client = genai_sdk.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=30_000),
            )
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=16384,
                )
            )
            text = response.text.strip()
            in_tok, out_tok, cached = usage_from_response(response, prompt, text)
            gen.update(
                output=text[:6000],
                usage_details={
                    'input': max(0, in_tok - cached),
                    'output': out_tok,
                    'input_cached_tokens': cached,
                },
            )
            return text

    @staticmethod
    def _validate_section_mcqs(items: list) -> list[dict]:
        """I-3: Drop malformed / duplicate MCQ rows before persisting."""
        valid: list[dict] = []
        seen_questions = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            question = (item.get('question') or '').strip()
            options = item.get('options')
            correct = (item.get('correct_option') or '').strip().upper()
            if not question or not isinstance(options, dict):
                continue
            option_keys = [k.upper() for k in options.keys()]
            option_values = [str(v).strip() for v in options.values() if str(v).strip()]
            if not {'A', 'B', 'C', 'D'}.issubset(set(option_keys)):
                continue
            if len(set(option_values)) < len(option_values):  # duplicate options
                continue
            if correct not in {'A', 'B', 'C', 'D'}:
                continue
            if question.lower() in seen_questions:  # duplicate question
                continue
            seen_questions.add(question.lower())
            valid.append({
                'question': question,
                'options': options,
                'correct_option': correct,
                'explanation': (item.get('explanation') or '').strip(),
            })
        return valid

    @staticmethod
    def _pad_mcqs(valid: list[dict], content: str, target: int) -> list[dict]:
        """Fill the remaining slots with content-grounded fallback MCQs."""
        fallback = IngestionService._fallback_section_mcqs_static(content, target - len(valid))
        return (valid + fallback)[:target]

    def _fallback_section_mcqs(self, content: str, count: int = 5) -> list[dict]:
        """G-8 + I-1: content-grounded fallback MCQs with varied correct answers."""
        return self._fallback_section_mcqs_static(content, count)

    @staticmethod
    def _fallback_section_mcqs_static(content: str, count: int = 5) -> list[dict]:
        """I-1: Draw stems from real sentences AND vary the correct option position
        deterministically from the content hash, so the 'correct answer is always
        C' learnable pattern is removed."""
        import hashlib as _hl
        clean_text = (content or '').replace('\n', ' ').strip()
        sentences = [s.strip() for s in clean_text.split('.') if len(s.strip()) > 20]
        digest = int(_hl.sha256(clean_text.encode('utf-8')).hexdigest(), 16)

        # Vary which stem the fallback uses per question.
        stems = [
            sentences[i][:80] if i < len(sentences) else f'the documented procedure ({i + 1})'
            for i in range(count)
        ]
        question_templates = [
            lambda s: f"According to this section, what is required regarding: '{s}'?",
            lambda s: f"Which statement is consistent with this section regarding '{s}'?",
            lambda s: f"To ensure compliance as described in this section for '{s}', the correct action is to:",
            lambda s: f"Which of the following best reflects the requirement in '{s}'?",
            lambda s: f"Based on the section covering '{s}', which action is correct?",
        ]
        option_banks = [
            ['Modifications may be made at operator discretion.',
             'Verbal instructions override the written procedure.',
             'Strict adherence to all documented procedures is mandatory.',
             'Documentation is only required when deviations occur.'],
            ['All actions must follow standard operating guidelines.',
             'Quality checks are optional when time is limited.',
             'Deviations do not require formal documentation.',
             'Untrained personnel may perform steps under supervision only.'],
            ['Perform steps without recording results.',
             'Delegate tasks to whoever is available.',
             'Only act when a supervisor is physically present.',
             'Follow the SOP and document all steps fully.'],
            ['Review and follow the documented procedure as written.',
             'Skip steps that seem unnecessary.',
             'Rely on verbal handover only.',
             'Record results only if the supervisor asks.'],
            ['Comply with the procedure and complete the required records.',
             'Proceed without records to save time.',
             'Ask a colleague to sign on your behalf.',
             'Postpone the task until a supervisor is present.'],
        ]
        explanations = [
            'SOPs mandate strict adherence; no unauthorised modifications are permitted.',
            'Standard guidelines are mandatory and non-negotiable for compliance.',
            'Procedures must be followed and fully documented to ensure traceability and compliance.',
            'The documented procedure is the source of truth for compliant action.',
            'Compliance requires following the procedure and completing all records.',
        ]

        letters = ['A', 'B', 'C', 'D']
        fallback = []
        for i in range(count):
            options = option_banks[i % len(option_banks)]
            # I-1: vary the correct answer position deterministically from the
            # content hash, so the 'correct answer is always C/A/D' pattern is gone.
            correct_idx = (digest + i) % 4
            fallback.append({
                'question': question_templates[i % len(question_templates)](stems[i]),
                'options': {letters[j]: options[j] for j in range(4)},
                'correct_option': letters[correct_idx],
                'explanation': explanations[i % len(explanations)],
            })
        return fallback

    def get_status(self, document_id: int):
        document = self.document_repository.get_by_id(document_id)
        if not document:
            raise ValueError('Document not found')

        parent_count = self.db.query(ParentChunk).filter(
            ParentChunk.document_id == document_id
        ).count()

        return {
            'document_id': document.id,
            'status': document.status,
            'parent_count': parent_count,
            'chunk_count': self.chunk_repository.count_by_document(document_id),
            'mcq_count': self.mcq_repository.count_by_document(document_id),
            'summary': document.summary,
            'failure_reason': document.failure_reason,
        }

    def recover_stale_ingestions(self, max_age_minutes: int = 120) -> int:
        """I-7: Mark documents stuck in a processing state as failed.

        If a Celery worker dies mid-ingestion, the document is left in
        'chunking' / 'embedding' / 'mcq_gen' forever. This scans for such rows
        older than ``max_age_minutes`` and flags them 'failed_stale' with a
        descriptive reason so the admin UI can surface them.

        Returns the number of documents recovered.
        """
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import or_

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        # I-7: use updated_at (last status change) as the staleness signal, NOT
        # created_at — a re-ingestion of an old document must not be flagged
        # stale merely because the row was created long ago.
        stale = self.db.query(Document).filter(
            Document.status.in_(['chunking', 'embedding', 'mcq_gen']),
            or_(
                Document.updated_at.is_(None),
                Document.updated_at < cutoff,
            ),
        ).all()

        recovered = 0
        for doc in stale:
            logger.warning(
                'Recovering stale ingestion: document %d stuck in status=%s since %s',
                doc.id, doc.status, doc.updated_at,
            )
            self.document_repository.update(
                doc,
                status='failed_stale',
                failure_reason=(
                    f'Ingestion stuck in \'{doc.status}\' for over {max_age_minutes} minutes '
                    f'(last updated {doc.updated_at}). Worker may have died; re-trigger processing.'
                ),
            )
            recovered += 1
        return recovered
