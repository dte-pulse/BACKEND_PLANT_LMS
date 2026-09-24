"""
Single token estimator shared across chunking, token logging and cost reports.

Previously the pipeline used four different heuristics (chars/4, words*1.3, ...)
which made chunk-size budgets and cost reports internally inconsistent. This
module is the single source of truth for token estimation. It uses the
word-count proxy (1 word ≈ 1.3 tokens for English) which is significantly more
accurate than chars/4 for pharmaceutical SOP text with abbreviations, chemical
formulae and numbered lists.
"""
import logging

logger = logging.getLogger(__name__)

# C-2: tiktoken is now in requirements.txt; the encoder is loaded once per
# process (get_encoding caches internally, but we avoid the repeated lookup).
_TIKTOKEN_ENCODING = "cl100k_base"
_ENCODER = None
_ENCODER_TRIED = False


def _get_encoder():
    global _ENCODER, _ENCODER_TRIED
    if not _ENCODER_TRIED:
        _ENCODER_TRIED = True
        try:
            import tiktoken  # type: ignore[import-not-found]
            _ENCODER = tiktoken.get_encoding(_TIKTOKEN_ENCODING)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"tiktoken unavailable — using word-count proxy: {e}")
            _ENCODER = None
    return _ENCODER


def estimate_tokens(text: str) -> int:
    """Count tokens in ``text`` with a real tokenizer (tiktoken, cl100k_base).

    Falls back to a word-count proxy (words × 1.3) when tiktoken is unavailable.
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return max(1, len(enc.encode(text)))
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"tiktoken encoding failed, using word proxy: {e}")
    return max(1, int(len(text.split()) * 1.3))


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Gemini 2.5 Flash pricing: $0.30/1M input, $2.50/1M output.

    Kept in sync with Langfuse's predefined ``gemini-2.5-flash`` price
    definition so the internal token ledger and Langfuse report the same
    numbers. Cached reads ($0.03/1M) are only distinguishable from real usage
    metadata, so the estimate path ignores them."""
    return (prompt_tokens * 0.30 + completion_tokens * 2.50) / 1_000_000


def langfuse_usage_details(response, prompt: str = '', text: str = '') -> dict:
    """Canonical Langfuse usage dict from a Gemini response.

    Single source of truth for the ``input`` / ``output`` / ``input_cached_tokens``
    keys Langfuse's predefined ``gemini-2.5-flash`` price definition matches
    verbatim. The cached portion is split out of ``input`` so the buckets stay
    mutually exclusive (Langfuse sums all types into the total). Falls back to
    word-count estimates when real usage metadata is absent.
    """
    in_tok, out_tok, cached = usage_from_response(response, prompt, text)
    return {
        'input': max(0, in_tok - cached),
        'output': out_tok,
        'input_cached_tokens': cached,
    }


def usage_from_response(response, fallback_prompt: str = '', fallback_text: str = '') -> tuple[int, int, int]:
    """Real token counts from a Gemini ``generate_content`` response.

    Reads ``response.usage_metadata`` (``prompt_token_count``,
    ``candidates_token_count``, ``cached_content_token_count``,
    ``thoughts_token_count``) so Langfuse cost is EXACT instead of estimated.
    Falls back to word-count estimates when the metadata is absent
    (mock/fallback paths).

    Returns ``(input_tokens, output_tokens, cached_input_tokens)``. Note the
    input count INCLUDES the cached portion — callers that also report
    ``input_cached_tokens`` should subtract it so the usage buckets stay
    mutually exclusive (Langfuse sums all types into the total).

    Thinking tokens: Gemini 2.5 Flash bills reasoning/thinking tokens at the
    OUTPUT rate (``total = prompt + candidates + thoughts``). Langfuse's
    predefined ``gemini-2.5-flash`` price definition has no separate thinking
    key, so we fold ``thoughts_token_count`` into the output count for an
    exact billed total.
    """
    usage = getattr(response, 'usage_metadata', None)
    if usage is not None:
        prompt_tok = int(getattr(usage, 'prompt_token_count', 0) or 0)
        output_tok = int(getattr(usage, 'candidates_token_count', 0) or 0)
        if not output_tok:
            output_tok = int(getattr(usage, 'response_token_count', 0) or 0)
        cached = int(getattr(usage, 'cached_content_token_count', 0) or 0)
        thoughts = int(getattr(usage, 'thoughts_token_count', 0) or 0)
        if prompt_tok or output_tok:
            return prompt_tok, output_tok + thoughts, cached
    return estimate_tokens(fallback_prompt), estimate_tokens(fallback_text), 0
