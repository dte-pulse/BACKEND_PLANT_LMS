"""
Mind Map Service
================
Builds a hierarchical mind map tree for a document that shows:
- Document root
- Parent chunk nodes (sections/topics)
- Child chunk nodes (sub-topics)
- User progress status on each node
"""
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.document import Document
from app.models.parent_chunk import ParentChunk
from app.models.chunk import Chunk

# If these models are available, they will be used for progress tracking
try:
    from app.models.parent_chunk_progress import ParentChunkProgress
except ImportError:
    ParentChunkProgress = None

try:
    from app.models.child_chunk_attempt import ChildChunkAttempt
except ImportError:
    ChildChunkAttempt = None

import re
from difflib import SequenceMatcher  # G-4: used for title similarity matching

# Single shared implementation (C-1): the recap must never reach titles, hashes
# or LLM prompts.
from app.utils.text_utils import strip_preceding_context as _strip_preceding_context


def _clean_node_title(text: str) -> str:
    """Extract a clean, meaningful title from raw chunk content.
    Strips [Preceding Section:] artifacts, HTML, markdown, and page-number fragments."""
    if not text:
        return ""
    # ── First: strip the RAG context prefix injected during chunking ──────────
    text = _strip_preceding_context(text)
    # Strip HTML tags
    cleaned = re.sub(r'<[^>]+>', '', text)
    # Strip page citations like |161|
    cleaned = re.sub(r'\|\d+\|?', '', cleaned)
    # Strip markdown bold/italic markers
    cleaned = re.sub(r'[*_]{1,3}', '', cleaned)
    # Strip fenced code blocks
    cleaned = re.sub(r'```.*?```', '', cleaned, flags=re.DOTALL)

    # Walk lines looking for the first meaningful prose line
    skip_patterns = re.compile(
        r'^(https?://|www\.|doi:|arXiv|[A-Z][a-z]+,\s[A-Z]\.)|'  # URLs / citations
        r'^\s*[\|\-\+]{2}|'                                          # table rows
        r'^\s*[\d]+\s*$|'                                            # lone numbers
        r'^\s*[A-Za-z]\.|'                                           # single-char list markers
        r'^CONTENTS\s*$|^INDEX\s*$|^Preceding Section',             # artifacts
        re.IGNORECASE
    )
    for raw_line in cleaned.split('\n'):
        line = raw_line.strip().lstrip('{}/*#`[]\r\t ')
        if len(line) < 8:
            continue
        if skip_patterns.search(line):
            continue
        # Return first truly meaningful line, trimmed to 60 chars
        return (line[:57].rstrip() + '...') if len(line) > 60 else line
    return "Section Topic"


def _extract_chapter_number(title: str) -> str:
    """Extract the top-level chapter number from a section title.
    '1.1 Purpose' → '1', '2.3 Architecture' → '2', 'Table of Contents' → '0'"""
    m = re.match(r'^(\d+)\.', title.strip())
    return m.group(1) if m else '0'


def _chapter_display_title(chapter_num: str, sections: list) -> str:
    """Derive a human-readable chapter title from the group of sections.
    Tries to find it from a Table of Contents chunk in the first section group."""
    if chapter_num == '0':
        return sections[0].title if sections else 'Preface'
    # Use the chapter number with a generic label derived from the first section title
    first_title = sections[0].title if sections else ''
    # Strip the sub-section prefix (e.g. '1.1 ' from '1.1 Purpose') to get the topic word
    topic = re.sub(r'^\d+\.\d+\s*', '', first_title).strip()
    return f"{chapter_num}. {topic.split()[0].capitalize() if topic else 'Chapter ' + chapter_num}"


def tree_regenerate_mode(db: Session, document) -> str:
    """Cheap pre-check (titles only): 'deterministic' needs no LLM call,
    'llm' requires the model — used by the regen endpoint to decide sync vs
    Celery dispatch (P1 #2)."""
    from app.models.parent_chunk import ParentChunk
    rows = db.query(ParentChunk.title).filter(
        ParentChunk.document_id == document.id
    ).all()
    titles = [r[0] or '' for r in rows]
    numbered = sum(1 for t in titles if re.match(r'^\d+\.\d+', t))
    if len(titles) > 0 and numbered / len(titles) >= 0.7:
        return 'deterministic'
    return 'llm'


def build_regenerated_tree(db: Session, document, user_id: int = 0) -> tuple[list, bool]:
    """Build a fresh concept tree for ``document`` (P1 #2 extraction).

    Encapsulates the M-5 outline budget and the M-7 deterministic-vs-LLM
    branch so both the (synchronous, cheap) deterministic regen path and the
    Celery LLM path share one implementation. Does NOT persist — callers
    decide where the tree lands (document.mind_map_json).

    Returns ``(tree, used_llm)`` — ``used_llm=False`` means the tree was built
    deterministically from the document's own numbered headings and required
    no model call.
    """
    from app.models.parent_chunk import ParentChunk
    from app.utils.text_utils import strip_preceding_context

    parent_chunks = db.query(ParentChunk).filter(
        ParentChunk.document_id == document.id
    ).order_by(ParentChunk.section_index).all()

    # M-5 fix: bound the total outline to the client's 14000-char budget with a
    # per-section cap so later sections are never silently dropped.
    MAX_OUTLINE_CHARS = 14000
    n_sections = max(1, len(parent_chunks))
    per_section_chars = max(400, MAX_OUTLINE_CHARS // n_sections)

    outline_parts = []
    used = 0
    for p in parent_chunks:
        part = f"Section: {p.title or f'Section {p.section_index}'}\n{strip_preceding_context(p.content or '')[:per_section_chars]}"
        if used + len(part) > MAX_OUTLINE_CHARS:
            break
        outline_parts.append(part)
        used += len(part)
    outline_text = "\n\n".join(outline_parts)

    # M-7 fix: numbered documents carry a document-accurate DETERMINISTIC
    # concept tree (built at ingestion from the doc's own X.Y headings, with
    # the real chapter titles).  Regenerating must NOT replace it with the
    # LLM's invented semantic tree — that is exactly what produced the wrong
    # 6-chapter mind map for doc 11 instead of the document's 10 chapters.
    # The LLM refresh is kept for non-numbered documents only.
    numbered_titles = sum(
        1 for p in parent_chunks if re.match(r'^\d+\.\d+', (p.title or ''))
    )
    is_numbered_doc = len(parent_chunks) > 0 and numbered_titles / len(parent_chunks) >= 0.7

    if is_numbered_doc:
        # Preserve the deterministic tree when already stored; otherwise (old
        # LLM tree from a pre-fix ingestion) rebuild one from the parent
        # titles so regenerate never reintroduces invented chapters.
        stored_tree = document.mind_map_json or []
        flat_subtopics = [
            st.get('title', '') for ch in stored_tree
            for st in (ch.get('children', []) if isinstance(ch, dict) else [])
        ]
        tree_is_deterministic = bool(flat_subtopics) and sum(
            1 for t in flat_subtopics if re.match(r'^\d+\.\d+', t)
        ) / len(flat_subtopics) >= 0.7
        if tree_is_deterministic:
            return stored_tree, False

        # Stale-tree recovery: group parents by their chapter number.  The
        # real chapter titles (e.g. '1. Introduction') are not persisted on
        # ParentChunk, so the fallback uses 'Chapter N' labels — acceptable
        # for recovery; a re-ingestion rebuilds the exact titles.
        from collections import OrderedDict
        chapters_map = OrderedDict()
        for p in parent_chunks:
            m = re.match(r'^(\d+)\.\d+', (p.title or ''))
            # Non-numbered parents (preface / intro) go to chapter '0' so
            # they are never silently dropped from the regenerated tree.
            ch_num = m.group(1) if m else '0'
            if ch_num not in chapters_map:
                chapters_map[ch_num] = {'title': f'Chapter {ch_num}', 'children': []}
            chapters_map[ch_num]['children'].append({'title': p.title, 'children': []})
        return list(chapters_map.values()), False

    from app.clients.llm_client import LLMClient
    llm = LLMClient()
    tree = llm.generate_mind_map_structure(outline_text, document.title, user_id=user_id)
    return tree, True


class MindMapService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    @staticmethod
    def _group_by_section_indexes(concept_tree: list, nodes: list) -> 'list[dict] | None':
        """Group flat parent nodes into chapters using an index-tagged tree (P2 #2).

        The tree generated at ingestion now carries explicit ``section_indexes``
        per chapter — direct references into the document's parent-chunk order —
        so chapters are built by indexing with ZERO fuzzy title matching. The
        LLM's job is reduced to chapter ORGANIZATION; displayed section titles
        remain the document's own.

        Returns None when the tree carries no usable index coverage (<70% of
        nodes mapped, or a legacy tree without indexes) — callers then fall
        back to the legacy fuzzy title-matching cascade.
        """
        from collections import OrderedDict

        if not isinstance(concept_tree, list) or not nodes:
            return None

        def _valid_int(v) -> bool:
            return isinstance(v, int) and not isinstance(v, bool)

        # chapter_title -> candidate section indexes (schema-tolerant)
        chapter_candidates: "OrderedDict[str, list[int]]" = OrderedDict()
        for chapter in concept_tree:
            if not isinstance(chapter, dict):
                continue
            ch_title = str(chapter.get('title') or 'Chapter')
            candidates: list[int] = []
            if isinstance(chapter.get('section_indexes'), list):
                candidates = [i for i in chapter['section_indexes'] if _valid_int(i)]
            else:
                # Legacy nested schema: children may carry a section_index each
                for sub in (chapter.get('children') or []):
                    if isinstance(sub, dict) and _valid_int(sub.get('section_index')):
                        candidates.append(sub['section_index'])
            chapter_candidates[ch_title] = candidates

        n = len(nodes)
        used: set[int] = set()
        chapters_idx: "OrderedDict[str, list[int]]" = OrderedDict()
        for ch_title, candidates in chapter_candidates.items():
            valid = sorted({i for i in candidates if 0 <= i < n})
            deduped = [i for i in valid if i not in used]
            if not deduped:
                continue  # chapter claims only already-used / out-of-range indexes
            used.update(deduped)
            chapters_idx[ch_title] = deduped

        if not used or len(used) / n < 0.7:
            return None  # insufficient index coverage — fall back to legacy

        result: list[dict] = []
        for ch_title, idxs in chapters_idx.items():
            children = [{**nodes[i], 'flat_parent_idx': i} for i in idxs]
            statuses = [s.get('status', 'locked') for s in children]
            if statuses and all(st == 'completed' for st in statuses):
                ch_status = 'completed'
            elif any(st in ('in_progress', 'completed') for st in statuses):
                ch_status = 'in_progress'
            else:
                ch_status = 'locked'
            result.append({
                'id': f'chapter_{len(result)}',
                'type': 'chapter',
                'title': ch_title,
                'status': ch_status,
                'knowledge_score': round(
                    sum(s.get('knowledge_score', 0.0) for s in children) / max(1, len(children)), 2),
                'children': children,
                'children_total': len(children),
                'children_completed': sum(1 for s in children if s.get('status') == 'completed'),
            })

        # Sections the tree didn't reference still surface (appendix) — same
        # contract as the legacy grouping path.
        unmapped = [{**nd, 'flat_parent_idx': i} for i, nd in enumerate(nodes) if i not in used]
        if unmapped:
            result.append({
                'id': 'chapter_appendix',
                'type': 'chapter',
                'title': 'Supporting Material',
                'status': 'in_progress',
                'knowledge_score': 0.0,
                'children': unmapped,
                'children_total': len(unmapped),
                'children_completed': sum(1 for s in unmapped if s.get('status') == 'completed'),
            })
        return result

    def build_mind_map(self, document_id: int) -> dict:
        from sqlalchemy.orm import defer
        document = self.db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {}

        document_title = document.title

        # 1. Fetch parents with deferred embeddings in one query
        parent_chunks = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
            ParentChunk.document_id == document_id
        ).order_by(ParentChunk.section_index).all()

        if not parent_chunks:
            # Fetch children with deferred embeddings in one query
            all_chunks = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
                Chunk.document_id == document_id
            ).order_by(Chunk.id).all()

            if not all_chunks:
                return {
                    'document_id': document_id,
                    'document_title': document_title,
                    'total_score': 0.0,
                    'total_parents': 0,
                    'completed_parents': 0,
                    'overall_progress_pct': 0.0,
                    'concept_tree': document.mind_map_json or [],
                    'nodes': [],
                    'message': 'No chunks found for this document.'
                }

            # Group flat chunks into synthetic parent modules of up to 5 chunks each
            chunk_groups = [all_chunks[i:i + 5] for i in range(0, len(all_chunks), 5)]
            synthetic_nodes = []
            for g_idx, group in enumerate(chunk_groups, start=1):
                children_nodes = []
                for c_idx, c in enumerate(group, start=1):
                    clean_title = _clean_node_title(c.content or "")
                    children_nodes.append({
                        'id': f'child_{c.id}',
                        'type': 'child',
                        'chunk_id': c.id,
                        'child_index': c_idx,
                        'title': clean_title,
                        'page_no': c.page_no,
                        'status': 'in_progress',
                        'knowledge_score': 0.0,
                        'attempt_count': 0,
                        'is_passed': False,
                    })

                first_title = _clean_node_title(group[0].content or "")
                synthetic_nodes.append({
                    'id': f'parent_syn_{g_idx}',
                    'type': 'parent',
                    'section_index': g_idx,
                    'title': f"Module {g_idx}: {first_title}",
                    'summary': f"Section overview covering {len(group)} topics.",
                    'page_start': group[0].page_no,
                    'page_end': group[-1].page_no,
                    'status': 'in_progress',
                    'knowledge_score': 0.0,
                    'children_total': len(group),
                    'children_completed': 0,
                    'children': children_nodes,
                })

            return {
                'document_id': document_id,
                'document_title': document_title,
                'total_score': 0.0,
                'total_parents': len(synthetic_nodes),
                'completed_parents': 0,
                'overall_progress_pct': 0.0,
                'concept_tree': document.mind_map_json or [],
                'nodes': synthetic_nodes,
            }

        # 2. Bulk fetch all children with deferred embeddings
        child_chunks_all = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
            Chunk.document_id == document_id
        ).order_by(Chunk.parent_chunk_id, Chunk.child_index).all()
        
        children_by_parent = {}
        for c in child_chunks_all:
            children_by_parent.setdefault(c.parent_chunk_id, []).append(c)

        # 3. Bulk fetch all parent progress records
        progress_by_parent = {}
        if ParentChunkProgress:
            progress_records = self.db.query(ParentChunkProgress).filter(
                ParentChunkProgress.user_id == self.user_id,
                ParentChunkProgress.document_id == document_id
            ).all()
            progress_by_parent = {p.parent_chunk_id: p for p in progress_records}

        # 4. Bulk fetch all child attempts for user and document
        attempts_by_child = {}
        if ChildChunkAttempt:
            attempts = self.db.query(ChildChunkAttempt).filter(
                ChildChunkAttempt.user_id == self.user_id,
                ChildChunkAttempt.document_id == document_id
            ).order_by(ChildChunkAttempt.attempt_number).all()
            for a in attempts:
                attempts_by_child.setdefault(a.child_chunk_id, []).append(a)

        # Query previous version to perform structural mind map diffing
        prev_doc = self.db.query(Document).filter(
            Document.code == document.code,
            Document.id != document.id
        ).order_by(Document.version.desc()).first()

        prev_parents_map = {}
        prev_children_map = {}
        prev_children = []
        if prev_doc:
            prev_parents = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
                ParentChunk.document_id == prev_doc.id
            ).all()
            prev_parents_map = {p.stable_id: p for p in prev_parents if p.stable_id}

            prev_children = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
                Chunk.document_id == prev_doc.id
            ).all()
            prev_children_map = {c.stable_id: c for c in prev_children if c.stable_id}

        nodes = []
        completed_parents_count = 0
        total_parents = len(parent_chunks)
        sum_scores = 0.0
        
        previous_parent_completed = True

        for parent in parent_chunks:
            parent_progress = progress_by_parent.get(parent.id)
            child_chunks = children_by_parent.get(parent.id, [])
            
            children_nodes = []
            children_total = len(child_chunks)
            children_completed = 0
            
            any_child_in_progress = False
            previous_child_completed = previous_parent_completed

            # M-3: children share the PARENT-derived mastery state so the mind
            # map and the structure endpoint report the same knowledge values.
            # Real per-child attempt counts are still surfaced.
            parent_passed = bool(parent_progress and parent_progress.is_completed)

            for child in child_chunks:
                # Calculate child stats from pre-fetched attempts list
                c_attempts = attempts_by_child.get(child.id, [])
                attempt_count = len(c_attempts)
                knowledge_score = 100.0 if parent_passed else 0.0
                is_passed = parent_passed

                # Determine child status
                if is_passed:
                    child_status = 'completed'
                    children_completed += 1
                elif attempt_count > 0:
                    child_status = 'in_progress'
                    any_child_in_progress = True
                else:
                    if previous_child_completed:
                        child_status = 'in_progress' # Unlocked
                    else:
                        child_status = 'locked'
                        
                previous_child_completed = is_passed

                clean_child_title = _clean_node_title(child.content or "")

                # Determine child node version diff status
                c_stable_id = child.stable_id
                prev_c = prev_children_map.get(c_stable_id) if c_stable_id else None
                if prev_c:
                    if prev_c.content_hash != child.content_hash:
                        child_ver_status = 'modified'
                    else:
                        child_ver_status = 'unchanged'
                else:
                    child_ver_status = 'new'

                children_nodes.append({
                    'id': f'child_{child.id}',
                    'node_id': child.stable_id or f'child_{child.id}',
                    'type': 'child',
                    'chunk_id': child.id,
                    'stable_id': child.stable_id,
                    'child_index': child.child_index,
                    'title': clean_child_title,
                    'page_no': child.page_no,
                    'status': child_status,
                    'knowledge_score': knowledge_score,
                    'attempt_count': attempt_count,
                    'is_passed': is_passed,
                    'version_status': child_ver_status,
                })
                
            # Determine parent status
            if children_total > 0 and children_completed == children_total:
                parent_status = 'completed'
                completed_parents_count += 1
            elif any_child_in_progress or (children_total > 0 and children_completed > 0):
                parent_status = 'in_progress'
            else:
                if previous_parent_completed:
                    parent_status = 'in_progress'
                else:
                    parent_status = 'locked'

            # G-13: Always compute parent score from children directly
            # (consistent, never mixes stale stored scores with live averages)
            if children_completed > 0 or any_child_in_progress:
                child_scores = [
                    node['knowledge_score'] for node in children_nodes
                    if node.get('knowledge_score', 0) > 0
                ]
                parent_score = round(sum(child_scores) / len(child_scores), 2) if child_scores else 0.0
                sum_scores += parent_score

            previous_parent_completed = (parent_status == 'completed')

            clean_parent_title = _clean_node_title(parent.title or f'Section {parent.section_index}')

            # Determine parent node version diff status
            p_stable_id = parent.stable_id
            prev_p = prev_parents_map.get(p_stable_id) if p_stable_id else None
            if prev_p:
                if prev_p.content_hash != parent.content_hash:
                    parent_ver_status = 'modified'
                else:
                    parent_ver_status = 'unchanged'
            else:
                parent_ver_status = 'new'

            nodes.append({
                'id': f'parent_{parent.id}',
                'node_id': parent.stable_id or f'parent_{parent.id}',
                'type': 'parent',
                'stable_id': parent.stable_id,
                'section_index': parent.section_index,
                'title': clean_parent_title,
                'summary': parent.summary or '',
                'page_start': parent.page_start,
                'page_end': parent.page_end,
                'status': parent_status,
                'knowledge_score': parent_progress.knowledge_score if parent_progress else 0.0,
                'children_total': children_total,
                'children_completed': children_completed,
                'children': children_nodes,
                'version_status': parent_ver_status,
            })

        # 5. Handle removed nodes from previous version
        if prev_doc:
            current_parent_stable_ids = {p.stable_id for p in parent_chunks if p.stable_id}
            removed_parents = [p for p in prev_parents_map.values() if p.stable_id not in current_parent_stable_ids]
            
            for p in removed_parents:
                removed_children = [c for c in prev_children if c.parent_chunk_id == p.id]
                removed_children_nodes = []
                for c in removed_children:
                    removed_children_nodes.append({
                        'id': f'child_removed_{c.id}',
                        'node_id': c.stable_id or f'child_{c.id}',
                        'type': 'child',
                        'chunk_id': c.id,
                        'stable_id': c.stable_id,
                        'child_index': c.child_index,
                        'title': f"[Removed] {_clean_node_title(c.content or '')}",
                        'page_no': c.page_no,
                        'status': 'locked',
                        'knowledge_score': 0.0,
                        'attempt_count': 0,
                        'is_passed': False,
                        'version_status': 'removed',
                    })
                
                nodes.append({
                    'id': f'parent_removed_{p.id}',
                    'node_id': p.stable_id or f'parent_{p.id}',
                    'type': 'parent',
                    'stable_id': p.stable_id,
                    'section_index': p.section_index,
                    'title': f"[Removed] {_clean_node_title(p.title or f'Section {p.section_index}')}",
                    'summary': p.summary or '',
                    'page_start': p.page_start,
                    'page_end': p.page_end,
                    'status': 'locked',
                    'knowledge_score': 0.0,
                    'children_total': len(removed_children),
                    'children_completed': 0,
                    'children': removed_children_nodes,
                    'version_status': 'removed',
                })

        # 6. Group flat nodes into chapter-level nodes for the mind map view
        # If we have an LLM-generated concept tree (document.mind_map_json), use it for clean grouping.
        # Otherwise fallback to regex-based chapter number grouping.
        grouped_nodes = []
        concept_tree = document.mind_map_json or []

        # M-6 fix: when the parent titles carry the DOCUMENT'S OWN numbered
        # section structure (e.g. "1.1 Purpose", "2.3 Architecture Diagram"),
        # that structure is authoritative — prefer the deterministic
        # chapter-number grouping so the mind map mirrors the source PDF's
        # chapters exactly.  The LLM concept tree tends to invent 4-6 semantic
        # chapter names that don't match the document (and fuzzy title matching
        # can then misplace sections), which is what made the doc-11 mind map
        # look "not accurate according to the document".
        numbered_titles = sum(
            1 for n in nodes if re.match(r'^\d+\.\d+', n.get('title', ''))
        )
        has_numbered_structure = len(nodes) > 0 and numbered_titles / len(nodes) >= 0.7

        # M-7: a DETERMINISTIC concept tree (built from the doc's own numbered
        # headings at ingestion) carries numbered sub-topics too — it is
        # document-accurate and should still drive the grouping (its chapter
        # titles are the real '1. Introduction' names).  Only the LLM semantic
        # tree gets bypassed for numbered documents.
        tree_is_deterministic = False
        if isinstance(concept_tree, list):
            flat_subtopics = [
                st.get('title', '') for ch in concept_tree
                for st in (ch.get('children', []) if isinstance(ch, dict) else [])
            ]
            if flat_subtopics:
                numbered_subtopics = sum(
                    1 for t in flat_subtopics if re.match(r'^\d+\.\d+', t)
                )
                tree_is_deterministic = numbered_subtopics / len(flat_subtopics) >= 0.7

        # P2 #2: prefer SECTION-INDEX binding — trees generated at ingestion
        # carry explicit ``section_indexes`` per chapter (direct references into
        # the parent-chunk order), so grouping needs no fuzzy matching and the
        # displayed titles stay the document's own. Falls back to the legacy
        # fuzzy title-matching cascade for trees generated before this upgrade.
        indexed_grouping = None
        if concept_tree and isinstance(concept_tree, list) and \
                (not has_numbered_structure or tree_is_deterministic):
            indexed_grouping = self._group_by_section_indexes(concept_tree, nodes)

        if indexed_grouping is not None:
            grouped_nodes = indexed_grouping
        elif concept_tree and isinstance(concept_tree, list) and \
                (not has_numbered_structure or tree_is_deterministic):
            used_node_ids = set()
            for chap_idx, chapter in enumerate(concept_tree):
                chap_title = chapter.get('title', f'Chapter {chap_idx + 1}')
                chap_sections = []
                
                sub_topics = chapter.get('children', [])
                for sub_topic in sub_topics:
                    sub_title = sub_topic.get('title', '')

                    # G-4: Improved title matching cascade:
                    # 1. Exact substring  2. SequenceMatcher ratio  3. Jaccard overlap
                    # This handles paraphrased/synonymous titles that pure Jaccard misses.
                    best_match_idx = -1
                    best_score = -1.0

                    sub_title_lower = sub_title.lower().strip()

                    for idx, node in enumerate(nodes):
                        if node['id'] in used_node_ids:
                            continue

                        db_title_lower = node['title'].lower().strip()

                        # Strategy 1: exact substring — strongest signal
                        if sub_title_lower in db_title_lower or db_title_lower in sub_title_lower:
                            score = 10.0
                        else:
                            # Strategy 2: SequenceMatcher (handles paraphrasing)
                            seq_score = SequenceMatcher(None, sub_title_lower, db_title_lower).ratio()
                            # Strategy 3: Jaccard on word tokens
                            sub_tokens = set(sub_title_lower.split())
                            db_tokens = set(db_title_lower.split())
                            intersection = sub_tokens.intersection(db_tokens)
                            jaccard = (
                                len(intersection) / max(1, len(sub_tokens.union(db_tokens)))
                                if intersection else 0.0
                            )
                            # Use the stronger of the two signals
                            score = max(seq_score, jaccard * 1.5)

                        if score > best_score:
                            best_score = score
                            best_match_idx = idx
                            
                    if best_match_idx != -1 and best_score > 0.0:
                        matched_node = nodes[best_match_idx]
                        used_node_ids.add(matched_node['id'])
                        
                        # Copy the matched node and override title with LLM sub-topic title for better layout alignment
                        node_to_add = {**matched_node, 'title': sub_title, 'flat_parent_idx': best_match_idx}
                        chap_sections.append(node_to_add)
                    else:
                        # Fallback: find any unused node
                        unused_nodes = [(i, n) for i, n in enumerate(nodes) if n['id'] not in used_node_ids]
                        if unused_nodes:
                            fallback_idx, matched_node = unused_nodes[0]
                            used_node_ids.add(matched_node['id'])
                            node_to_add = {**matched_node, 'title': sub_title, 'flat_parent_idx': fallback_idx}
                            chap_sections.append(node_to_add)
                
                if chap_sections:
                    section_statuses = [s.get('status', 'locked') for s in chap_sections]
                    if all(st == 'completed' for st in section_statuses):
                        chap_status = 'completed'
                    elif any(st in ('in_progress', 'completed') for st in section_statuses):
                        chap_status = 'in_progress'
                    else:
                        chap_status = 'locked'
                        
                    avg_score = round(sum(s.get('knowledge_score', 0.0) for s in chap_sections) / max(1, len(chap_sections)), 2)
                    
                    grouped_nodes.append({
                        'id': f'chapter_{chap_idx}',
                        'type': 'chapter',
                        'title': chap_title,
                        'status': chap_status,
                        'knowledge_score': avg_score,
                        'children': chap_sections,
                        'children_total': len(chap_sections),
                        'children_completed': sum(1 for s in chap_sections if s.get('status') == 'completed'),
                    })
            
            # Any remaining unmapped nodes are appended under a general chapter
            unmapped_nodes = [{**n, 'flat_parent_idx': idx} for idx, n in enumerate(nodes) if n['id'] not in used_node_ids]
            if unmapped_nodes:
                grouped_nodes.append({
                    'id': 'chapter_appendix',
                    'type': 'chapter',
                    'title': 'Supporting Material',
                    'status': 'in_progress',
                    'knowledge_score': 0.0,
                    'children': unmapped_nodes,
                    'children_total': len(unmapped_nodes),
                    'children_completed': sum(1 for s in unmapped_nodes if s.get('status') == 'completed'),
                })
        else:
            from collections import OrderedDict
            chapter_map = OrderedDict()
            for flat_idx, node in enumerate(nodes):
                raw_title = node.get('title', '')
                chap_num = _extract_chapter_number(raw_title)
                if chap_num not in chapter_map:
                    chapter_map[chap_num] = []
                # Inject the flat index so the frontend can use it for navigation
                chapter_map[chap_num].append({**node, 'flat_parent_idx': flat_idx})

            for chap_num, sections in chapter_map.items():
                # Derive a display title for the chapter group
                if chap_num == '0':
                    chap_title = sections[0]['title'] if sections else 'Preface'
                else:
                    first_section_title = sections[0].get('title', '')
                    # Strip the sub-section prefix "1.1 " to get the topic keyword
                    topic = re.sub(r'^\d+\.\d+\s*', '', first_section_title).strip()
                    first_word = topic.split()[0].capitalize() if topic else f'Chapter {chap_num}'
                    chap_title = f"{chap_num}. {first_word}"

                # Chapter status = worst/best of its sections
                section_statuses = [s.get('status', 'locked') for s in sections]
                if all(st == 'completed' for st in section_statuses):
                    chap_status = 'completed'
                elif any(st in ('in_progress', 'completed') for st in section_statuses):
                    chap_status = 'in_progress'
                else:
                    chap_status = 'locked'

                avg_score = round(
                    sum(s.get('knowledge_score', 0.0) for s in sections) / max(1, len(sections)), 2
                )

                grouped_nodes.append({
                    'id': f'chapter_{chap_num}',
                    'type': 'chapter',
                    'title': chap_title,
                    'status': chap_status,
                    'knowledge_score': avg_score,
                    # children here are the SECTIONS (parent chunks), not text sub-chunks
                    'children': sections,
                    'children_total': len(sections),
                    'children_completed': sum(1 for s in sections if s.get('status') == 'completed'),
                })

        overall_progress_pct = (completed_parents_count / total_parents * 100) if total_parents > 0 else 0.0
        # Average over all parents that have any score data
        parents_with_score = sum(1 for n in nodes if n.get('knowledge_score', 0) > 0)
        total_score = (sum_scores / parents_with_score) if parents_with_score > 0 else 0.0

        return {
            'document_id': document_id,
            'document_title': document_title,
            'total_score': round(total_score, 2),
            'total_parents': total_parents,
            'completed_parents': completed_parents_count,
            'overall_progress_pct': round(overall_progress_pct, 2),
            'concept_tree': document.mind_map_json or [],
            'nodes': grouped_nodes,     # chapter-grouped view for the mind map UI
            'flat_nodes': nodes,        # flat view (section_index-indexed) for other uses
        }

