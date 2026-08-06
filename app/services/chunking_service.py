import re
from typing import Optional

class ChunkingService:
    def __init__(
        self,
        parent_target_tokens: int = 2000,
        child_target_tokens: int = 500,
        child_overlap_tokens: int = 50,
        max_chunk_tokens: int = 8000,
        **kwargs
    ):
        # Handle backward compatibility mappings for parent_target_words etc.
        # 1 token is approximately 1.33 words for standard English texts
        if 'parent_target_words' in kwargs:
            self.parent_target_tokens = int(kwargs['parent_target_words'] * 1.33)
        else:
            self.parent_target_tokens = parent_target_tokens
            
        if 'child_target_words' in kwargs:
            self.child_target_tokens = int(kwargs['child_target_words'] * 1.33)
        else:
            self.child_target_tokens = child_target_tokens
            
        if 'child_overlap_words' in kwargs:
            self.child_overlap_tokens = int(kwargs['child_overlap_words'] * 1.33)
        else:
            self.child_overlap_tokens = child_overlap_tokens
            
        self.max_chunk_tokens = max_chunk_tokens

    @staticmethod
    def count_tokens(text: str) -> int:
        """Heuristic character-to-token count (1 token ≈ 4 characters)."""
        return max(1, len(text) // 4)

    def split_pages(self, pages: list[dict], toc: list = None) -> dict:
        full_text = '\n'.join(p['text'] for p in pages)

        # Build a token-offset → page_no lookup so we can assign accurate page numbers
        page_map = []   # list of (cumulative_token_start, page_no)
        current_idx = 0
        for p in pages:
            page_map.append((current_idx, p.get('page_no', 1)))
            current_idx += self.count_tokens(p['text'])

        parents = None
        if toc:
            try:
                parents = self._split_by_toc(pages, toc)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Error splitting by PDF TOC: {e}")

        if not parents:
            parents = self._split_by_headings(full_text, page_map)

        # Filter ghost sections (< 40 tokens) — avoids headers/footers becoming parents
        parents = [p for p in parents if self.count_tokens(p['content']) >= 40]

        result = {'parents': []}
        global_chunk_idx = 1
        global_token_offset = 0   # cumulative token count across all parents

        for p_idx, parent in enumerate(parents, 1):
            # Prepend preceding section context for parent continuity (Priority 8)
            content_with_context = parent['content']
            if p_idx > 1:
                prev_parent = parents[p_idx - 2]
                prev_paras = [p for p in prev_parent['content'].split('\n\n') if p.strip()]
                if prev_paras:
                    recap = f"[Preceding Section: {prev_parent['title']}]\n... {prev_paras[-1]}\n\n"
                    content_with_context = recap + parent['content']

            parent_dict = {
                'section_index': p_idx,
                'title': parent['title'],
                'content': content_with_context,
                'page_start': parent['page_start'],
                'page_end': parent['page_end'],
                'token_count': self.count_tokens(content_with_context),
                'children': []
            }

            children_texts = self._split_into_children(content_with_context, parent['page_start'])

            # Track token offset within this parent so each child gets an accurate page_no
            child_token_offset = global_token_offset
            for c_idx, child_text in enumerate(children_texts, 1):
                child_page = self._word_offset_to_page(child_token_offset, page_map)
                parent_dict['children'].append({
                    'child_index': c_idx,
                    'page_no': child_page,
                    'chunk_index': global_chunk_idx,
                    'content': child_text,
                    'token_count': self.count_tokens(child_text),
                    'hierarchy_path': f"{parent['title']}"  # Priority 3
                })
                child_token_offset += self.count_tokens(child_text)
                global_chunk_idx += 1

            global_token_offset += self.count_tokens(content_with_context)
            result['parents'].append(parent_dict)

        return result

    def _word_offset_to_page(self, token_offset: int, page_map: list) -> int:
        """Return the page_no for a given cumulative token offset."""
        found_page = page_map[0][1] if page_map else 1
        for start_idx, p_no in reversed(page_map):
            if token_offset >= start_idx:
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
            token_idx = self.count_tokens(full_text[:char_idx])
            found_page = page_map[0][1] if page_map else 1
            for start_idx, p_no in reversed(page_map):
                if token_idx >= start_idx:
                    found_page = p_no
                    break
            return found_page

        if not all_matches:
            # No markdown headings — split by token target but preserve paragraph boundaries
            paragraphs = [p for p in full_text.split('\n\n') if p.strip()]
            sections = []
            current_paras = []
            current_tokens = 0

            for para in paragraphs:
                para_token_count = self.count_tokens(para)
                if current_tokens + para_token_count > self.parent_target_tokens and current_paras:
                    chunk_text = '\n\n'.join(current_paras)
                    first_sent = current_paras[0].replace('\n', ' ').strip().split('.')[0][:60].strip()
                    title = first_sent if len(first_sent) >= 5 else f'Section {len(sections) + 1}'
                    page_start = get_page_no(full_text.find(current_paras[0]))
                    page_end = get_page_no(full_text.find(current_paras[-1]) + len(current_paras[-1]))
                    sections.append({
                        'title': title,
                        'content': chunk_text,
                        'page_start': page_start,
                        'page_end': page_end
                    })
                    current_paras = [para]
                    current_tokens = para_token_count
                else:
                    current_paras.append(para)
                    current_tokens += para_token_count

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

        # Adaptive level selection: dominant level repeating >= 2 times
        from collections import Counter
        level_counts = Counter(len(m.group(1)) for m in all_matches if len(m.group(1)) <= 4)
        if not level_counts:
            return []

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

            # Skip pure TOC/index pages
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

        # Rebalancing pass: merge adjacent tiny subsections (Priority 4)
        rebalanced_sections = []
        min_tokens = self.parent_target_tokens // 4
        for sec in raw_sections:
            if rebalanced_sections and self.count_tokens(sec['content']) < min_tokens:
                rebalanced_sections[-1]['content'] += "\n\n" + sec['content']
                rebalanced_sections[-1]['page_end'] = max(rebalanced_sections[-1]['page_end'], sec['page_end'])
                rebalanced_sections[-1]['char_end'] = sec['char_end']
            else:
                rebalanced_sections.append(sec)
        raw_sections = rebalanced_sections

        # ── Pass 2: sub-split any oversized section at dominant+1 ─────────────
        sub_level = dominant + 1
        max_tokens = self.parent_target_tokens * 2
        final_sections = []

        for sec in raw_sections:
            if self.count_tokens(sec['content']) <= max_tokens:
                final_sections.append(sec)
                continue

            # Scan full_text slice for exact sub_level headings
            sec_text = full_text[sec['char_start']:sec['char_end']]
            sub_re = re.compile(r'^#{%d}\s+(.+)$' % sub_level, re.MULTILINE)
            sub_matches = list(sub_re.finditer(sec_text))

            if not sub_matches:
                final_sections.append(sec)
                continue

            # Reject sub-split if any resulting sub-section would be < 80 tokens
            sub_token_counts = []
            for j, sm in enumerate(sub_matches):
                sub_end = sub_matches[j+1].start() if j + 1 < len(sub_matches) else len(sec_text)
                sub_token_counts.append(self.count_tokens(sec_text[sm.start():sub_end]))
            if any(t < 80 for t in sub_token_counts):
                final_sections.append(sec)
                continue

            # Preamble before first sub-heading
            if sub_matches[0].start() > 0:
                pre = sec_text[:sub_matches[0].start()].strip()
                if pre and self.count_tokens(pre) >= 40:
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
        section_tokens = self.count_tokens(section_content)
        if section_tokens <= self.child_target_tokens:
            return [section_content.strip()] if section_content.strip() else []

        paragraphs = [p for p in section_content.split('\n\n') if p.strip()]
        chunks = []
        current_paras = []
        current_token_count = 0

        # Protect tables and list structures from splitting (Priority 2)
        def is_table_or_list(paragraph: str) -> bool:
            lines = paragraph.strip().splitlines()
            if not lines:
                return False
            if any(line.strip().startswith('|') for line in lines):
                return True
            if all(re.match(r'^(\d+\.|\*|-|\u2022)\s+', line.strip()) for line in lines if line.strip()):
                return True
            return False

        for para in paragraphs:
            para_len = self.count_tokens(para)
            if not para.strip():
                continue

            # Hard ceiling check: force-split oversized blocks to fit API context window (Priority 1)
            if para_len > self.max_chunk_tokens:
                if current_paras:
                    chunks.append('\n\n'.join(current_paras))
                    current_paras = []
                    current_token_count = 0
                
                # Split words proportionally by token count
                words = para.split()
                temp_chunk = []
                for w in words:
                    temp_chunk.append(w)
                    if self.count_tokens(' '.join(temp_chunk)) >= self.child_target_tokens:
                        chunks.append(' '.join(temp_chunk))
                        temp_chunk = []
                if temp_chunk:
                    chunks.append(' '.join(temp_chunk))
                continue

            if current_token_count + para_len >= self.child_target_tokens and current_paras:
                chunks.append('\n\n'.join(current_paras))
                # Child-level overlap: carry over the last paragraph for context
                current_paras = current_paras[-1:] + [para]
                current_token_count = self.count_tokens(current_paras[0]) + para_len
            elif para_len >= self.child_target_tokens:
                if current_paras:
                    chunks.append('\n\n'.join(current_paras))
                    current_paras = []
                    current_token_count = 0

                # Protect tables and lists from structural sentence breaks
                if is_table_or_list(para):
                    chunks.append(para.strip())
                    continue

                # Sentence splitter using abbreviation-aware pattern matching (Priority 6)
                sentences = self._split_into_sentences(para)
                if len(sentences) <= 1:
                    para_words = para.split()
                    temp_words = []
                    for w in para_words:
                        temp_words.append(w)
                        if self.count_tokens(' '.join(temp_words)) >= self.child_target_tokens:
                            chunks.append(' '.join(temp_words))
                            temp_words = []
                    if temp_words:
                        chunks.append(' '.join(temp_words))
                else:
                    temp_sentences = []
                    temp_tokens = 0
                    for sent in sentences:
                        sent_tokens = self.count_tokens(sent)
                        if temp_tokens + sent_tokens >= self.child_target_tokens and temp_sentences:
                            chunks.append(' '.join(temp_sentences))
                            temp_sentences = [sent]
                            temp_tokens = sent_tokens
                        else:
                            temp_sentences.append(sent)
                            temp_tokens += sent_tokens
                    if temp_sentences:
                        current_paras = [' '.join(temp_sentences)]
                        current_token_count = temp_tokens
            else:
                current_paras.append(para)
                current_token_count += para_len

        if current_paras:
            chunks.append('\n\n'.join(current_paras))

        return [c.strip() for c in chunks if c.strip()]

    def _split_into_sentences(self, text: str) -> list[str]:
        """Sentence splitter that prevents false breaks on decimal numbers and common abbreviations."""
        abbrev_pattern = r'\b(eg|ie|fig|dr|mr|mrs|ms|dept|sop|vol|no|vs|approx|min|max|temp|std)\.'
        raw_sentences = re.split(r'(?<=[.!?])\s+', text)
        
        sentences = []
        temp_sent = ""
        
        for s in raw_sentences:
            if not s.strip():
                continue
            if temp_sent:
                temp_sent = temp_sent + " " + s
            else:
                temp_sent = s
                
            # Keep merging sentences if we detect an abbreviation period
            if re.search(abbrev_pattern, temp_sent, re.IGNORECASE):
                continue
            # Keep merging if it ends with a single letter period (initial)
            if re.search(r'\b[A-Za-z]\.$', temp_sent):
                continue
            
            sentences.append(temp_sent)
            temp_sent = ""
            
        if temp_sent:
            sentences.append(temp_sent)
            
        return [s.strip() for s in sentences if s.strip()]

    def _split_by_toc(self, pages: list[dict], toc: list) -> list[dict]:
        total_pages = len(pages)
        if total_pages == 0:
            return []

        # Filter and sort bookmarks (level <= 3 represents major parts/chapters)
        filtered_toc = [item for item in toc if isinstance(item, list) and len(item) >= 3 and item[0] <= 3]
        filtered_toc.sort(key=lambda x: x[2])  # sort by page_no

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
            # Validate non-trivial content density (Priority 3)
            if pre_text and self.count_tokens(pre_text) >= 30:
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

            # Validate non-trivial content density (Priority 3)
            if section_content and self.count_tokens(section_content) >= 30:
                sections.append({
                    'title': title,
                    'content': section_content,
                    'page_start': p_start,
                    'page_end': p_end
                })

        return sections
