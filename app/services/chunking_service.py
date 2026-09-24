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
        """Estimate token count via the shared pipeline estimator (C-2 fix).
        1 word ≈ 1.3 tokens for English — significantly more accurate than
        the previous char//4 heuristic for pharmaceutical SOP text with
        abbreviations, chemical formulae, and numbered lists.  (G-2 fix)"""
        from app.utils.tokenizer import estimate_tokens
        return estimate_tokens(text)

    def split_pages(self, pages: list[dict], toc: list = None, llm_client = None) -> dict:
        # Join pages with a PARAGRAPH break: DOCX pagination splits at paragraph
        # boundaries, so a '\n' join here merged the paragraphs that straddled a
        # page edge back into one line — semantic clustering (which segments on
        # blank lines) then saw one giant paragraph per document and never
        # split. Heading regexes and token-offset page maps are insensitive to
        # \n vs \n\n, so this is safe for PDFs too.
        full_text = '\n\n'.join(p['text'] for p in pages)

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
            parents = self._split_by_headings(full_text, page_map, llm_client=llm_client, pages=pages)

        # Filter ghost sections (< 40 tokens) — avoids headers/footers becoming parents
        parents = [p for p in parents if self.count_tokens(p['content']) >= 40]

        result = {'parents': []}
        global_chunk_idx = 1
        global_token_offset = 0   # cumulative token count across all parents

        for p_idx, parent in enumerate(parents, 1):
            # C-1 fix: the preceding-section recap is no longer prepended to
            # parent or child content at all. Children are split from clean
            # content, and the recap is never embedded, hashed, stored or fed to
            # the LLM. (Legacy rows that still contain a recap are handled
            # defensively by strip_preceding_context at read time.)
            clean_content = parent['content']

            # C-4 fix: token offsets are computed on the CLEAN content so page
            # attribution is never shifted by a recap.
            parent_dict = {
                'section_index': p_idx,
                'title': parent['title'],
                'content': clean_content,
                'page_start': parent['page_start'],
                'page_end': parent['page_end'],
                'token_count': self.count_tokens(clean_content),
                'children': [],
                # M-7: carry the document's real chapter heading (e.g. '1. Introduction')
                # so the mind-map concept tree can use exact chapter names.
                'chapter_num': parent.get('chapter_num'),
                'chapter_title': parent.get('chapter_title'),
            }

            children_texts = self._split_into_children(clean_content, parent['page_start'])

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

            global_token_offset += self.count_tokens(clean_content)
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

    def _split_by_headings(self, full_text: str, page_map: list, llm_client = None, pages: list = None) -> list[dict]:
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

        # Prefer plain-text numbered sub-sections ("1.1 Purpose", "2.3 Architecture")
        # when the document has a real X.Y structure — these are authoritative
        # and must NOT be re-merged by the tiny-section pass below (which is
        # designed for LLM-invented titles in the blind fallback, and would
        # otherwise collapse 1.2/1.3 together and cascade later sections).
        numbered = self._split_by_numbered_headings(full_text, page_map, pages or [])
        if numbered:
            return numbered

        if not all_matches:
            # No markdown headings — P2 #5: embed children and CLUSTER them into
            # topic-coherent sections (one LLM call titles them all), instead of
            # blind 2000-token batching that cut topics mid-thought. Falls back
            # to the blind path when embeddings are unavailable.
            sections = self._split_by_semantic_clustering(full_text, page_map, llm_client)
            if not sections:
                sections = self._split_blind(full_text, page_map, llm_client, sections)
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

        # ── Pass 1: split at dominant and sub-levels ──────────────────────────
        matches_pass1 = [m for m in all_matches if len(m.group(1)) <= dominant + 1]

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

        # ── G-12: Merge tiny adjacent sections to prevent ghost parent nodes ───────────────
        # Sections below MIN_MERGE_TOKENS are merged into the next section
        # (or the previous one if they are the last in the list).  This avoids
        # stub sections that produce near-empty MCQs and unhelpful mind-map nodes.
        MIN_MERGE_TOKENS = 150
        merged: list[dict] = []
        for sec in raw_sections:
            if merged and self.count_tokens(sec['content']) < MIN_MERGE_TOKENS:
                # Current section is tiny — absorb it into the previous one
                prev = merged[-1]
                prev['content'] = prev['content'] + '\n\n' + sec['content']
                prev['page_end'] = sec.get('page_end', prev['page_end'])
                # C-3 fix: keep char_end in sync so pass-2 sub-splitting operates
                # on the correct character window.
                prev['char_end'] = sec.get('char_end', prev.get('char_end', 0))
            elif merged and self.count_tokens(merged[-1]['content']) < MIN_MERGE_TOKENS:
                # Previous section is tiny — absorb it into the current one
                prev = merged.pop()
                merged.append({
                    'title': sec['title'],
                    'content': prev['content'] + '\n\n' + sec['content'],
                    'char_start': prev.get('char_start', sec.get('char_start', 0)),
                    'char_end': sec.get('char_end', 0),
                    'page_start': prev.get('page_start', sec.get('page_start', 1)),
                    'page_end': sec.get('page_end', 1),
                })
            else:
                merged.append(sec)
        raw_sections = merged

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
                # G-11: Token-bounded overlap — carry at most child_overlap_tokens
                # worth of text from the end of the previous chunk (sentence-trimmed).
                overlap_bound = max(1, int(self.child_overlap_tokens / 1.3))  # words
                overlap_para = current_paras[-1] if current_paras else ''
                overlap_words = overlap_para.split()
                if len(overlap_words) > overlap_bound:
                    overlap_para = ' '.join(overlap_words[-overlap_bound:])
                current_paras = [overlap_para, para] if overlap_para.strip() else [para]
                current_token_count = self.count_tokens(overlap_para) + para_len
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

    def _split_by_numbered_headings(self, full_text: str, page_map: list, pages: list = None) -> list[dict]:
        """Detect plain-text numbered headings ("1.1 Purpose", "2. System Overview")
        in PDFs that have no bookmark outline.  Handles both raw lines and lines
        the extraction service already converted to markdown ("### **1.1 Purpose**").

        Splits at the sub-section level (X.Y) when the document uses one,
        otherwise at the chapter level (X).  The table-of-contents page (a page
        where most non-empty lines look like heading lines) is detected and
        excluded so its entries never become parent sections.

        Returns [] when no reliable numbered structure is found so the caller
        falls back to the markdown/heading path or blind token splitting.
        """
        pages = pages or []
        text_by_page = {p.get('page_no', 1): p.get('text', '') for p in pages}

        # Tolerate markdown markers around the number/title: "### **1.1 Purpose**"
        # NB: re.MULTILINE is compiled IN (flags arg), not passed to finditer —
        # Pattern.finditer(string, pos, endpos, flags) would treat a raw flag
        # value as the start position and silently match nothing.
        head_re = re.compile(
            r'^#{0,6}\s*(?:\*{0,3}\s*)?'
            r'(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+'
            r'([A-Z][^\n#*]{2,90}?)\s*(?:\*{1,3})?\s*$',
            re.MULTILINE,
        )

        def heading_like(line: str) -> bool:
            # Must match the SAME filters used for the real heading collection
            # below (short title, no trailing sentence punctuation) — otherwise
            # numbered LIST items ("1. Mix the solution for ten minutes.") are
            # counted as headings, a dense body page is misflagged as a TOC
            # page, and its real sub-sections are dropped entirely.
            m = head_re.match(line.strip())
            if not m:
                return False
            title = (m.group(2) or '').strip()
            if not title or len(line.strip()) > 80:
                return False
            return title[-1:] not in '.!?;:'

        # ── 1. Detect TOC pages ─────────────────────────────────────────────
        # A real TOC page differs from a body page in TWO ways: most of its
        # non-empty lines are heading-like AND the heading lines are mostly
        # CONSECUTIVE (no prose paragraphs interleaved).  Body pages repeat
        # chapter headings but always separate them with paragraphs, so the
        # consecutive-heading signal cleanly distinguishes the two — the pure
        # density test alone would flag dense body pages (e.g. 5 headings + a
        # few short paragraphs) and skip the first sub-sections.
        toc_pages = set()
        for p_no, text in text_by_page.items():
            lines = [l for l in text.splitlines() if l.strip()]
            if len(lines) < 5:
                continue
            flags = [bool(heading_like(l)) for l in lines]
            head_count = sum(flags)
            consecutive = sum(1 for a, b in zip(flags, flags[1:]) if a and b)
            if head_count >= 5 and head_count / len(lines) > 0.5 and consecutive >= 3:
                toc_pages.add(p_no)

        def get_page_no(char_idx: int) -> int:
            token_idx = self.count_tokens(full_text[:char_idx])
            found_page = page_map[0][1] if page_map else 1
            for start_idx, p_no in reversed(page_map):
                if token_idx >= start_idx:
                    found_page = p_no
                    break
            return found_page

        # ── 2. Collect heading matches on body pages (TOC page excluded) ─────
        all_heads = []
        for m in head_re.finditer(full_text):
            if get_page_no(m.start()) not in toc_pages:
                num = m.group(1)
                title = (m.group(2) or '').strip()
                if not title:
                    continue
                # Reject prose-like list items ("3.1 The first step is...")
                if title[-1:] in '.!?;:' or len(title) > 70:
                    continue
                level = num.count('.') + 1
                # Re-add the separator dot for chapter-level headings: the
                # regex's optional '\.?' consumed it in '1. Introduction'
                # (subsection numbers like '1.1' keep their own dot).
                display = f'{num} {title}' if level > 1 else f'{num}. {title}'
                all_heads.append({
                    'start': m.start(),
                    'end': m.end(),
                    'num': num,
                    'level': level,
                    'title': display,
                })

        if len(all_heads) < 2:
            return []

        # ── 3. Monotonicity guard: real numbered headings advance in order ────
        #     (chapter, section) must be non-decreasing for >70% of adjacent pairs.
        def num_tuple(num: str):
            parts = [int(p) for p in num.split('.')]
            return tuple(parts + [0] * (3 - len(parts)))

        ordered = sorted(all_heads, key=lambda h: h['start'])
        ordered_nums = [num_tuple(h['num']) for h in ordered]
        monotonic = sum(
            1 for a, b in zip(ordered_nums, ordered_nums[1:]) if b >= a
        )
        if len(ordered_nums) > 1 and monotonic / (len(ordered_nums) - 1) < 0.7:
            return []

        # ── 3b. Anchor level selection ──────────────────────────────────────
        # Prefer the SHALLOWEST sub-level that repeats >= 2 times.  A doc with
        # both 1.1 and 1.1.1 headings must anchor at X.Y only (mixed-depth
        # parents would produce a confusing TOC).
        level_counts = {}
        for h in ordered:
            if h['level'] >= 2:
                level_counts[h['level']] = level_counts.get(h['level'], 0) + 1
        if level_counts:
            anchor_level = min(
                (lvl for lvl, cnt in level_counts.items() if cnt >= 2),
                default=min(level_counts.keys()),
            )
            subsections = [h for h in ordered if h['level'] == anchor_level]
        else:
            subsections = []

        # Map each X.Y section to its parent chapter heading (e.g. '1. Introduction')
        # so the mind map / concept tree can use the DOCUMENT's real chapter names
        # instead of guessing from the first sub-topic word (reviewer M-7 fix).
        chapter_title_by_num = {}
        for h in ordered:
            if h['level'] == 1:
                chapter_title_by_num[h['num']] = h['title']

        sections = []

        if len(subsections) >= 2:
            # Sub-section level: one parent per X.Y section, matching the doc TOC.
            anchors = subsections
            for i, h in enumerate(anchors):
                end = anchors[i + 1]['start'] if i + 1 < len(anchors) else len(full_text)
                content = full_text[h['start']:end].strip()
                # Drop chapter-marker lines (e.g. '## 2. System Overview') that appear
                # right before the first sub-section of a chapter.  Guard against
                # numbered LIST items ('1. Mix the solution') — real chapter
                # headings are short and end without sentence punctuation.
                kept = []
                for l in content.splitlines():
                    s = l.strip()
                    if not s:
                        kept.append(l)
                        continue
                    m = head_re.match(s)
                    if (
                        m and m.group(1).count('.') == 0
                        and len(s) <= 70
                        and (m.group(2) or '').rstrip()[-1:] not in '.!?;:'
                    ):
                        continue  # chapter-only heading line — structural marker
                    kept.append(l)
                content = '\n'.join(kept).strip()
                if not content:
                    continue
                ch_num = h['num'].split('.')[0]
                sections.append({
                    'title': h['title'],
                    'content': content,
                    'page_start': get_page_no(h['start']),
                    'page_end': get_page_no(max(h['start'], end - 1)),
                    'chapter_num': ch_num,
                    'chapter_title': chapter_title_by_num.get(ch_num, ''),
                })

            # Preamble before the first sub-section (cover/TOC page text filtered out)
            pre_lines = []
            for l in full_text[:anchors[0]['start']].splitlines():
                s = l.strip()
                if not s:
                    continue
                m = head_re.match(s)
                if m or re.match(r'^#{1,6}\s*Table of Contents\b', s, re.IGNORECASE):
                    continue
                pre_lines.append(l)
            pre = '\n'.join(pre_lines).strip()
            if pre and self.count_tokens(pre) >= 40:
                sections.insert(0, {
                    'title': 'Introduction',
                    'content': pre,
                    'page_start': page_map[0][1] if page_map else 1,
                    'page_end': get_page_no(anchors[0]['start']),
                })
            return sections

        if len(ordered) >= 2:
            # Chapter level only (document without sub-sections)
            anchors = ordered
            seen_titles = set()
            for i, h in enumerate(anchors):
                end = anchors[i + 1]['start'] if i + 1 < len(anchors) else len(full_text)
                content = full_text[h['start']:end].strip()
                if not content:
                    continue
                # M-8: dedupe — a chapter can appear in a small (undetected) TOC
                # page AND in the body; keep the first occurrence only.
                if h['title'].lower() in seen_titles:
                    continue
                seen_titles.add(h['title'].lower())
                sections.append({
                    'title': h['title'],
                    'content': content,
                    'page_start': get_page_no(h['start']),
                    'page_end': get_page_no(max(h['start'], end - 1)),
                    # Chapter-level numbered docs ("1. Purpose", no X.Y subsections)
                    # carry chapter metadata too, so the ingestion deterministic
                    # mind-map gate and structure classification see the same
                    # structure markers the X.Y branch produces.
                    'chapter_num': h['num'].split('.')[0],
                    'chapter_title': h['title'],
                })
            return sections

        return []

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

    # ── P2 #5: Semantic clustering + batched titles (unstructured docs) ────────

    # Greedy agglomerative clustering on cosine similarity of child embeddings.
    # A child joins the current cluster while similarity to the cluster's running
    # mean stays above the threshold; a drop starts a new cluster. This is the
    # cheapest coherent-topic segmentation that needs no external clustering lib.
    CLUSTER_SIM_THRESHOLD = 0.30
    CLUSTER_MIN_TOKENS = 150    # below this, absorb into the previous cluster
    CLUSTER_MAX_TOKENS_CAP = 2  # hard-split clusters exceeding 2× parent target

    def _page_no_for_char(self, full_text: str, page_map: list, char_idx: int) -> int:
        """Page number for a character offset (token-offset lookup over page_map).
        Class-level twin of the ``get_page_no`` closure inside _split_by_headings,
        so the clustering/blind helpers can attribute pages without a closure."""
        token_idx = self.count_tokens(full_text[:char_idx])
        found_page = page_map[0][1] if page_map else 1
        for start_idx, p_no in reversed(page_map):
            if token_idx >= start_idx:
                found_page = p_no
                break
        return found_page

    def _cluster_min_tokens(self) -> int:
        """Minimum cluster size (floor 40, scaling to ~8% of the parent target).
        Clusters smaller than this are absorbed into their predecessor — but the
        band must scale with parent_target_tokens, otherwise a fixed floor either
        dissolves real topics (small targets) or never merges (large ones).

        The floor is 40 — NOT higher — because split_pages ghost-filters parents
        under 40 tokens: absorbing a cluster into a band whose total still lands
        below that line would DELETE content rather than merge it."""
        return max(40, int(self.parent_target_tokens * 0.08))

    def _split_blind(
        self, full_text: str, page_map: list, llm_client,
        precomputed_sections: list | None = None,
    ) -> list[dict]:
        """The legacy fallback: batch ~parent_target_tokens of paragraphs, one
        LLM title call per batch (or first-sentence title without an LLM).
        Kept as the no-embeddings degradation path for P2 #5.

        ``precomputed_sections``: sections whose titles still need to be filled
        in (the clustering path pre-populates content and returns early on any
        LLM failure — this pass then completes titles via generate_section_title
        exactly as before)."""
        paragraphs = [p for p in full_text.split('\n\n') if p.strip()]
        sections = precomputed_sections if precomputed_sections is not None else []
        if not paragraphs:
            return sections

        if precomputed_sections is not None:
            # Complete titles for pre-clustered sections (one call each — this
            # is the rare degraded path; the happy path batches instead).
            for sec in sections:
                if llm_client and not sec['title']:
                    sec['title'] = llm_client.generate_section_title(sec['content'])
                if not sec['title']:
                    first_sent = sec['content'].replace('\n', ' ').strip().split('.')[0][:60].strip()
                    sec['title'] = first_sent if len(first_sent) >= 5 else 'Section'
            return sections

        current_paras: list[str] = []
        current_tokens = 0

        def _flush():
            chunk_text = '\n\n'.join(current_paras)
            if llm_client:
                title = llm_client.generate_section_title(chunk_text)
            else:
                first_sent = current_paras[0].replace('\n', ' ').strip().split('.')[0][:60].strip()
                title = first_sent if len(first_sent) >= 5 else f'Section {len(sections) + 1}'
            page_start = self._page_no_for_char(
                full_text, page_map, full_text.find(current_paras[0]))
            page_end = self._page_no_for_char(
                full_text, page_map,
                full_text.find(current_paras[-1]) + len(current_paras[-1]))
            sections.append({
                'title': title,
                'content': chunk_text,
                'page_start': page_start,
                'page_end': page_end,
            })

        for para in paragraphs:
            para_token_count = self.count_tokens(para)
            if current_tokens + para_token_count > self.parent_target_tokens and current_paras:
                _flush()
                current_paras = [para]
                current_tokens = para_token_count
            else:
                current_paras.append(para)
                current_tokens += para_token_count

        if current_paras:
            _flush()
        return sections

    def _split_by_semantic_clustering(
        self, full_text: str, page_map: list, llm_client,
    ) -> list[dict]:
        """P2 #5 — topic-coherent sections for unstructured documents.

        Pipeline: paragraph-split → embed each paragraph (ONE batched call via
        embed_texts) → greedy agglomerative clustering on cosine similarity →
        token-band cleanup (split oversized, absorb tiny) → titles for ALL
        sections in ONE LLM call → page attribution. Falls back cleanly (returns
        [] or degrades to per-section titles) so ingestion never fails here."""
        paragraphs = [p for p in full_text.split('\n\n') if p.strip()]
        if not paragraphs:
            return []

        if llm_client is None or not getattr(llm_client, 'embedding_client', None):
            return []
        embed_client = llm_client.embedding_client
        if not getattr(embed_client, 'use_real', False):
            return []

        def _embed(emb_client, text):
            try:
                return emb_client.embed_text(text)
            except Exception:
                return None

        # 1. Embed paragraphs (small sequential batches keep requests bounded;
        #    failures → None → that paragraph clusters by position only).
        batch_size = 16
        vecs: list = []
        for i in range(0, len(paragraphs), batch_size):
            batch = paragraphs[i:i + batch_size]
            if hasattr(embed_client, 'embed_texts'):
                try:
                    vecs.extend(embed_client.embed_texts(batch))
                    continue
                except Exception:
                    pass  # fall through to per-item embedding
            vecs.extend(_embed(embed_client, t) for t in batch)
        if not any(v is not None for v in vecs):
            return []

        def _cos(a, b) -> float:
            from app.services.rag_service import _cosine_similarity
            return _cosine_similarity(a, b)

        def _valid(v) -> bool:
            return v is not None and sum(abs(x) for x in v) > 1e-9

        # 2. Greedy agglomerative clustering in document order.
        clusters: list[list[int]] = []
        current: list[int] = []
        current_sum: list | None = None
        for idx, vec in enumerate(vecs):
            joinable = _valid(vec) and current_sum is not None and (
                _cos(vec, [s / len(current) for s in current_sum]) >= self.CLUSTER_SIM_THRESHOLD
            )
            if current and joinable:
                current.append(idx)
                current_sum = [a + b for a, b in zip(current_sum, vecs[idx])]
            else:
                if current:
                    clusters.append(current)
                current = [idx]
                current_sum = list(vec) if _valid(vec) else None
        if current:
            clusters.append(current)

        # 3. Token-band cleanup: split oversized clusters at paragraph seams,
        #    absorb tiny clusters into their predecessor.
        def _cluster_tokens(idxs: list[int]) -> int:
            return sum(self.count_tokens(paragraphs[i]) for i in idxs)

        rebuilt: list[list[int]] = []
        for cl in clusters:
            if _cluster_tokens(cl) > self.parent_target_tokens * self.CLUSTER_MAX_TOKENS_CAP:
                part: list[int] = []
                part_tokens = 0
                for i in cl:
                    t = self.count_tokens(paragraphs[i])
                    if part and part_tokens + t > self.parent_target_tokens:
                        rebuilt.append(part)
                        part, part_tokens = [i], t
                    else:
                        part.append(i)
                        part_tokens += t
                if part:
                    rebuilt.append(part)
            else:
                rebuilt.append(cl)

        min_tokens = self._cluster_min_tokens()
        bands: list[list[int]] = []
        for cl in rebuilt:
            if bands and _cluster_tokens(cl) < min_tokens:
                bands[-1].extend(cl)
            elif bands and _cluster_tokens(bands[-1]) < min_tokens:
                bands[-1].extend(cl)
            else:
                bands.append(cl)
        clusters = bands or [list(range(len(paragraphs)))]

        # 4. Assemble sections with page attribution; titles filled by ONE
        #    batched LLM call (fallback: per-section / first-word).
        def _page_of(idxs: list[int]) -> tuple[int, int]:
            start_char = len('\n\n'.join(paragraphs[:idxs[0]]))
            if idxs[0] > 0:
                start_char += 2 * idxs[0]
            end_char = start_char + len('\n\n'.join(paragraphs[idxs[0]:idxs[-1] + 1]))
            return (
                self._page_no_for_char(full_text, page_map, start_char),
                self._page_no_for_char(full_text, page_map, end_char),
            )

        sections = []
        for cl in clusters:
            content = '\n\n'.join(paragraphs[i] for i in cl)
            page_start, page_end = _page_of(cl)
            sections.append({
                'title': '',  # filled below
                'content': content,
                'page_start': page_start,
                'page_end': page_end,
            })

        if llm_client and hasattr(llm_client, 'generate_batched_section_titles'):
            try:
                titles = llm_client.generate_batched_section_titles(
                    [s['content'] for s in sections]
                )
                if titles and len(titles) == len(sections):
                    for sec, t in zip(sections, titles):
                        sec['title'] = t
            except Exception:
                pass

        if any(not s['title'] for s in sections):
            return self._split_blind(full_text, page_map, llm_client, sections)
        return sections
