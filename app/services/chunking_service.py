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

    def split_pages(self, pages: list[dict]) -> dict:
        full_text = '\n'.join(p['text'] for p in pages)

        # Build a word-offset → page_no lookup so we can assign accurate page numbers
        page_map = []   # list of (cumulative_word_start, page_no)
        current_idx = 0
        for p in pages:
            page_map.append((current_idx, p.get('page_no', 1)))
            current_idx += len(p['text'].split())

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

    def _split_by_headings(self, full_text: str, page_map: list) -> list[dict]:
        matches = list(re.finditer(r'^#{1,2}\s+(.+)$', full_text, re.MULTILINE))
        
        sections = []
        
        def get_page_no(char_idx: int) -> int:
            word_idx = len(full_text[:char_idx].split())
            found_page = page_map[0][1] if page_map else 1
            for start_idx, p_no in reversed(page_map):
                if word_idx >= start_idx:
                    found_page = p_no
                    break
            return found_page
            
        if not matches:
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
            
        for i, match in enumerate(matches):
            start_pos = match.start()
            end_pos = matches[i+1].start() if i + 1 < len(matches) else len(full_text)
            
            section_content = full_text[start_pos:end_pos].strip()
            title = self._extract_heading_title(section_content)
            
            page_start = get_page_no(start_pos)
            page_end = get_page_no(end_pos)
            
            sections.append({
                'title': title,
                'content': section_content,
                'page_start': page_start,
                'page_end': page_end
            })
            
        if matches and matches[0].start() > 0:
            pre_text = full_text[:matches[0].start()].strip()
            if pre_text:
                page_start = get_page_no(0)
                page_end = get_page_no(matches[0].start())
                sections.insert(0, {
                    'title': 'Introduction',
                    'content': pre_text,
                    'page_start': page_start,
                    'page_end': page_end
                })
                
        return sections

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
