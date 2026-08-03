import re
from typing import Optional

class ChunkingService:
    def __init__(
        self,
        parent_target_words: int = 1500,
        child_target_words: int = 380,
        child_overlap_words: int = 40,
    ):
        self.parent_target_words = parent_target_words
        self.child_target_words = child_target_words
        self.child_overlap_words = child_overlap_words

    def split_pages(self, pages: list[dict], toc: list = None) -> dict:
        full_text = '\n'.join(p['text'] for p in pages)

        # Build a word-offset → page_no lookup so we can assign accurate page numbers
        page_map = []   # list of (cumulative_word_start, page_no)
        current_idx = 0
        for p in pages:
            page_map.append((current_idx, p.get('page_no', 1)))
            current_idx += len(p['text'].split())

        parents = None
        if toc:
            try:
                parents = self._split_by_toc(pages, toc)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Error splitting by PDF TOC: {e}")

        if not parents:
            parents = self._split_by_headings(full_text, page_map)

        # Filter ghost sections (< 30 words) — avoids table-header fragments becoming parents
        parents = [p for p in parents if len(p['content'].split()) >= 30]

        result = {'parents': []}
        global_chunk_idx = 1
        global_word_offset = 0   # cumulative word count across all parents (for page tracking)

        for p_idx, parent in enumerate(parents, 1):
            parent_dict = {
                'section_index': p_idx,
                'title': parent['title'],
                'content': parent['content'],
                'page_start': parent['page_start'],
                'page_end': parent['page_end'],
                'token_count': len(parent['content'].split()),
                'children': []
            }

            children_texts = self._split_into_children(parent['content'], parent['page_start'])

            # Track word offset within this parent so each child gets an accurate page_no
            child_word_offset = global_word_offset
            for c_idx, child_text in enumerate(children_texts, 1):
                child_page = self._word_offset_to_page(child_word_offset, page_map)
                parent_dict['children'].append({
                    'child_index': c_idx,
                    'page_no': child_page,
                    'chunk_index': global_chunk_idx,
                    'content': child_text,
                    'token_count': len(child_text.split())
                })
                child_word_offset += len(child_text.split())
                global_chunk_idx += 1

            global_word_offset += len(parent['content'].split())
            result['parents'].append(parent_dict)

        return result

    def _word_offset_to_page(self, word_offset: int, page_map: list) -> int:
        """Return the page_no for a given cumulative word offset."""
        found_page = page_map[0][1] if page_map else 1
        for start_idx, p_no in reversed(page_map):
            if word_offset >= start_idx:
                found_page = p_no
                break
        return found_page

    @staticmethod
    def _clean_title(raw: str) -> str:
        """Strip markdown/HTML formatting from a heading title."""
        import re
        t = raw.strip()
        # Remove markdown heading hashes
        t = re.sub(r'^#{1,6}\s*', '', t)
        # Remove bold/italic markers
        t = re.sub(r'\*{1,3}|_{1,3}', '', t)
        # Remove HTML tags (e.g. <u>, <br>)
        t = re.sub(r'<[^>]+>', '', t)
        # Collapse spaces
        t = re.sub(r'\s+', ' ', t).strip()
        return t[:120] if t else 'Section'

    def _split_by_headings(self, full_text: str, page_map: list) -> list[dict]:
        # Detect all headings up to h6 so we can inspect the hierarchy
        all_matches = list(re.finditer(r'^(#{1,6})\s+(.+)$', full_text, re.MULTILINE))

        sections = []

        def get_page_no(char_idx: int) -> int:
            word_idx = len(full_text[:char_idx].split())
            found_page = page_map[0][1] if page_map else 1
            for start_idx, p_no in reversed(page_map):
                if word_idx >= start_idx:
                    found_page = p_no
                    break
            return found_page

        if not all_matches:
            # No markdown headings — split by word count but preserve text structure
            paragraphs = [p for p in full_text.split('\n\n') if p.strip()]
            sections = []
            current_paras = []
            current_words = 0

            for para in paragraphs:
                para_word_count = len(para.split())
                if current_words + para_word_count > self.parent_target_words and current_paras:
                    chunk_text = '\n\n'.join(current_paras)
                    # Derive a meaningful title from the first sentence of this section
                    first_sent = current_paras[0].replace('\n', ' ').strip().split('.')[0][:60].strip()
                    title = first_sent if len(first_sent) >= 5 else f'Section {len(sections) + 1}'
                    pre_offset = sum(len(p.split()) for p in paragraphs[:paragraphs.index(current_paras[0])])
                    page_start = get_page_no(sum(len(c) + 2 for c in paragraphs[:paragraphs.index(current_paras[0])]))
                    page_end = get_page_no(sum(len(c) + 2 for c in paragraphs[:paragraphs.index(current_paras[0]) + len(current_paras)]))
                    sections.append({
                        'title': title,
                        'content': chunk_text,
                        'page_start': page_start,
                        'page_end': page_end
                    })
                    current_paras = [para]
                    current_words = para_word_count
                else:
                    current_paras.append(para)
                    current_words += para_word_count

            if current_paras:
                chunk_text = '\n\n'.join(current_paras)
                first_sent = current_paras[0].replace('\n', ' ').strip().split('.')[0][:60].strip()
                title = first_sent if len(first_sent) >= 5 else f'Section {len(sections) + 1}'
                sections.append({
                    'title': title,
                    'content': chunk_text,
                    'page_start': page_map[0][1] if page_map else 1,
                    'page_end': page_map[-1][1] if page_map else 1
                })
            return sections

        # Adaptive level selection:
        # Use the most common shallow heading level as the chapter split boundary.
        # h1 is often just the title page; we want the most common real chapter level.
        from collections import Counter
        level_counts = Counter(len(m.group(1)) for m in all_matches if len(m.group(1)) <= 4)
        if not level_counts:
            return []

        # Dominant = shallowest heading level that appears >= 2 times (a repeated chapter heading).
        # Fall back to the globally shallowest level if nothing repeats.
        dominant = min(
            (lvl for lvl, cnt in level_counts.items() if cnt >= 2),
            default=min(level_counts.keys())
        )

        # ── Pass 1: split at the dominant level only ──────────────────────────
        matches_pass1 = [m for m in all_matches if len(m.group(1)) <= dominant]

        raw_sections = []
        for i, match in enumerate(matches_pass1):
            start_pos = match.start()
            end_pos = matches_pass1[i+1].start() if i + 1 < len(matches_pass1) else len(full_text)
            section_content = full_text[start_pos:end_pos].strip()

            # Skip pure TOC/index pages: >60% of lines are markdown table rows
            content_lines = [l for l in section_content.splitlines() if l.strip()]
            table_lines = sum(1 for l in content_lines if l.strip().startswith('|'))
            if content_lines and table_lines / len(content_lines) > 0.6:
                continue

            raw_sections.append({
                'title': self._clean_title(match.group(0)),
                'content': section_content,
                'char_start': start_pos,
                'char_end': end_pos,
                'page_start': get_page_no(start_pos),
                'page_end': get_page_no(end_pos),
            })

        # Handle pre-heading preamble
        if matches_pass1 and matches_pass1[0].start() > 0:
            pre_text = full_text[:matches_pass1[0].start()].strip()
            if pre_text:
                raw_sections.insert(0, {
                    'title': 'Introduction',
                    'content': pre_text,
                    'char_start': 0,
                    'char_end': matches_pass1[0].start(),
                    'page_start': get_page_no(0),
                    'page_end': get_page_no(matches_pass1[0].start()),
                })

        # ── Pass 2: sub-split any oversized section at dominant+1 ─────────────
        # A section is "oversized" if it exceeds 2× the parent target word count.
        sub_level = dominant + 1
        max_words = self.parent_target_words * 2
        final_sections = []

        for sec in raw_sections:
            if len(sec['content'].split()) <= max_words:
                final_sections.append(sec)
                continue

            # Scan full_text slice for exact sub_level headings
            sec_text = full_text[sec['char_start']:sec['char_end']]
            sub_re = re.compile(r'^#{%d}\s+(.+)$' % sub_level, re.MULTILINE)
            sub_matches = list(sub_re.finditer(sec_text))

            if not sub_matches:
                final_sections.append(sec)
                continue

            # Reject sub-split if any resulting sub-section would be < 80 words
            sub_word_counts = []
            for j, sm in enumerate(sub_matches):
                sub_end = sub_matches[j+1].start() if j + 1 < len(sub_matches) else len(sec_text)
                sub_word_counts.append(len(sec_text[sm.start():sub_end].split()))
            if any(w < 80 for w in sub_word_counts):
                final_sections.append(sec)
                continue

            # Preamble before first sub-heading (keep under parent title)
            if sub_matches[0].start() > 0:
                pre = sec_text[:sub_matches[0].start()].strip()
                if pre and len(pre.split()) >= 30:
                    pre_end_abs = sec['char_start'] + sub_matches[0].start()
                    final_sections.append({
                        'title': sec['title'],
                        'content': pre,
                        'page_start': sec['page_start'],
                        'page_end': get_page_no(pre_end_abs),
                    })

            for j, sm in enumerate(sub_matches):
                sub_start = sm.start()
                sub_end = sub_matches[j+1].start() if j + 1 < len(sub_matches) else len(sec_text)
                sub_content = sec_text[sub_start:sub_end].strip()

                # Skip TOC-style sub-sections
                sub_lines = [l for l in sub_content.splitlines() if l.strip()]
                sub_table = sum(1 for l in sub_lines if l.strip().startswith('|'))
                if sub_lines and sub_table / len(sub_lines) > 0.6:
                    continue

                abs_start = sec['char_start'] + sub_start
                abs_end   = sec['char_start'] + sub_end
                final_sections.append({
                    'title': self._clean_title(sm.group(0)),
                    'content': sub_content,
                    'page_start': get_page_no(abs_start),
                    'page_end': get_page_no(abs_end),
                })

        return final_sections

    def _split_into_children(self, section_content: str, page_no: int) -> list[str]:
        words = section_content.split()
        if len(words) <= self.child_target_words:
            return [section_content.strip()] if section_content.strip() else []

        paragraphs = [p for p in section_content.split('\n\n') if p.strip()]
        chunks = []
        current_paras = []
        current_word_count = 0

        for para in paragraphs:
            para_words = para.split()
            para_len = len(para_words)
            if not para_words:
                continue

            if current_word_count + para_len >= self.child_target_words and current_paras:
                chunks.append('\n\n'.join(current_paras))
                # overlap: keep last paragraph for context
                current_paras = current_paras[-1:] + [para]
                current_word_count = len(current_paras[0].split()) + para_len
            elif para_len >= self.child_target_words:
                # Large single paragraph — split by sentences (or word fallback if no periods)
                if current_paras:
                    chunks.append('\n\n'.join(current_paras))
                    current_paras = []
                    current_word_count = 0
                sentences = para.split('. ')
                if len(sentences) <= 1:
                    for i in range(0, len(para_words), self.child_target_words):
                        sub_words = para_words[i:i + self.child_target_words]
                        if sub_words:
                            chunks.append(' '.join(sub_words))
                else:
                    temp_sentences = []
                    temp_words = 0
                    for i, sent in enumerate(sentences):
                        sent_words = sent.split()
                        if not sent_words:
                            continue
                        if i < len(sentences) - 1:
                            sent = sent.rstrip() + '.'
                        if temp_words + len(sent_words) >= self.child_target_words and temp_sentences:
                            chunks.append(' '.join(temp_sentences))
                            temp_sentences = [sent]
                            temp_words = len(sent_words)
                        else:
                            temp_sentences.append(sent)
                            temp_words += len(sent_words)
                    if temp_sentences:
                        current_paras = [' '.join(temp_sentences)]
                        current_word_count = temp_words
            else:
                current_paras.append(para)
                current_word_count += para_len

        if current_paras:
            chunks.append('\n\n'.join(current_paras))

        return [c.strip() for c in chunks if c.strip()]

    def _extract_heading_title(self, text: str) -> str:
        first_line = text.strip().split('\n')[0].strip()
        match = re.match(r'^#{1,3}\s+(.+)$', first_line)
        return match.group(1).strip() if match else first_line[:80]

    def _split_by_toc(self, pages: list[dict], toc: list) -> list[dict]:
        total_pages = len(pages)
        if total_pages == 0:
            return []

        # Filter and sort bookmarks (level <= 3 represents major parts, chapters, subsections)
        filtered_toc = [item for item in toc if isinstance(item, list) and len(item) >= 3 and item[0] <= 3]
        filtered_toc.sort(key=lambda x: x[2])  # sort by page_no

        # Remove duplicate page entries, keeping the first occurrence (closest to root)
        unique_toc = []
        seen_pages = set()
        for level, title, p_no in filtered_toc:
            if p_no not in seen_pages:
                unique_toc.append((title, p_no))
                seen_pages.add(p_no)

        if not unique_toc:
            return []

        sections = []
        
        # Insert Introduction if first bookmark starts after page 1
        if unique_toc[0][1] > 1:
            p_start = 1
            p_end = unique_toc[0][1] - 1
            section_pages = [p for p in pages if p_start <= p.get('page_no', 1) <= p_end]
            pre_text = '\n\n'.join(p['text'] for p in section_pages).strip()
            if pre_text:
                sections.append({
                    'title': 'Introduction',
                    'content': pre_text,
                    'page_start': p_start,
                    'page_end': p_end
                })

        for idx, (title, p_start) in enumerate(unique_toc):
            p_end = unique_toc[idx+1][1] - 1 if idx + 1 < len(unique_toc) else total_pages
            p_start = max(1, p_start)
            p_end = min(total_pages, max(p_start, p_end))

            section_pages = [p for p in pages if p_start <= p.get('page_no', 1) <= p_end]
            section_content = '\n\n'.join(p['text'] for p in section_pages).strip()

            if section_content:
                sections.append({
                    'title': title,
                    'content': section_content,
                    'page_start': p_start,
                    'page_end': p_end
                })

        return sections
