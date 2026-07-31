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

def _clean_node_title(text: str) -> str:
    """Extract a clean, meaningful title from raw chunk content.
    Skips bibliography lines, URL lines, page-number fragments, and code artifacts."""
    if not text:
        return ""
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
        r'^CONTENTS\s*$|^INDEX\s*$',
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


class MindMapService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

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

            for child in child_chunks:
                # Calculate child stats from pre-fetched attempts list
                c_attempts = attempts_by_child.get(child.id, [])
                attempt_count = len(c_attempts)
                knowledge_score = 0.0
                is_passed = False
                
                if attempt_count > 0:
                    correct = sum(1 for a in c_attempts if a.is_correct)
                    knowledge_score = round((correct / attempt_count) * 100, 2)
                    if knowledge_score >= 80.0:
                        is_passed = True

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

                children_nodes.append({
                    'id': f'child_{child.id}',
                    'type': 'child',
                    'chunk_id': child.id,
                    'child_index': child.child_index,
                    'title': clean_child_title,
                    'page_no': child.page_no,
                    'status': child_status,
                    'knowledge_score': knowledge_score,
                    'attempt_count': attempt_count,
                    'is_passed': is_passed,
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

            # Accumulate score for ALL parents that have any attempt data
            if children_completed > 0 or any_child_in_progress:
                parent_score = parent_progress.knowledge_score if parent_progress else (
                    round(sum(
                        node['knowledge_score'] for node in children_nodes
                        if node.get('knowledge_score', 0) > 0
                    ) / max(1, sum(1 for node in children_nodes if node.get('knowledge_score', 0) > 0)), 2)
                    if any(node.get('knowledge_score', 0) > 0 for node in children_nodes) else 0.0
                )
                sum_scores += parent_score

            previous_parent_completed = (parent_status == 'completed')

            clean_parent_title = _clean_node_title(parent.title or f'Section {parent.section_index}')

            nodes.append({
                'id': f'parent_{parent.id}',
                'type': 'parent',
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
            'nodes': nodes,
        }
