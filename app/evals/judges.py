"""
LLM-as-a-Judge evals — Gemini judges the quality of the app's own LLM outputs.

Each judge:
- runs only when Langfuse is configured AND the eval sample rate draws a hit
  (evals add a second Gemini call per evaluated item — cost must be sampled),
- is itself traced as an ``evaluator`` observation (its own LLM call nests
  inside), so eval spend shows up in Langfuse cost dashboards,
- attaches the score to the current trace (or the given observation object).

Production monitoring tip: Langfuse also supports server-side, observation-level
LLM-as-a-Judge evaluators configured in the UI (Evaluators page) with an LLM
Connection — those run on a sample of live traffic without any app code. The
judges here give you deterministic, code-controlled evals that work today.
"""
import logging

from app.clients.langfuse_client import (
    score_current_trace,
    score_trace,
    should_sample,
    langfuse_observation,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

_JUDGE_MODEL = 'gemini-2.5-flash'


def _judge_llm():
    from app.clients.llm_client import LLMClient
    return LLMClient()


def _judge_ready(llm) -> bool:
    """Eval gating: sample hit + Gemini available (LLMClient constructed once)."""
    return bool(llm.model) and should_sample(settings.langfuse_evals_sample_rate)


def judge_qa_groundedness(question: str, answer: str, context_snippet: str,
                          trace_obs=None) -> float | None:
    """Score 0–1 whether the answer is grounded in the retrieved context.

    Mirrors the RAG contract: answers must be based ONLY on the provided
    excerpts and cite [Page X, Chunk Y]. Judges hallucinated or out-of-scope
    answers as low, well-grounded answers as high.
    """
    llm = _judge_llm()
    if not _judge_ready(llm):
        return None
    context = (context_snippet or '')[:4000]
    prompt = (
        "You are an evaluation judge for a pharmaceutical SOP question-answering system. "
        "Score the ANSWER's GROUNDEDNESS on a 0.0 to 1.0 scale.\n\n"
        "Rubric:\n"
        "- 1.0: Fully grounded — every claim in the answer is supported by the excerpts.\n"
        "- 0.7: Mostly grounded — minor elaboration that does not contradict the excerpts.\n"
        "- 0.4: Partially grounded — some claims go beyond the excerpts or are unverifiable.\n"
        "- 0.0: Hallucinated — key claims are not in the excerpts, or the answer is off-topic.\n\n"
        "Return ONLY a JSON object: {\"score\": 0.0-1.0, \"reason\": \"<one sentence>\"}\n\n"
        f"EXCERPTS:\n{context}\n\nQUESTION: {question}\n\nANSWER:\n{answer[:3000]}\n"
    )
    data = _run_judge(llm, prompt, 'judge-qa-groundedness', 'eval_judge_qa')
    if not isinstance(data, dict) or 'score' not in data:
        return None
    score = max(0.0, min(1.0, float(data['score'])))
    comment = str(data.get('reason', ''))[:500] or None
    if trace_obs is not None:
        score_trace(trace_obs, 'qa-groundedness', score, 'NUMERIC', comment)
    else:
        score_current_trace('qa-groundedness', score, 'NUMERIC', comment)
    return score


def judge_mcq_quality(items: list[dict], content: str, trace_obs=None) -> float | None:
    """Score 0–1 the quality of a generated MCQ batch for a section.

    Judges: questions must be answerable from the content, options plausible,
    correct_option unambiguous, no duplicates — the exact invariants the
    ingestion validator enforces mechanically, judged for semantic quality.
    """
    llm = _judge_llm()
    if not _judge_ready(llm):
        return None
    import json as _json
    sample = items[:3]
    prompt = (
        "You are an evaluation judge for MCQ questions generated from pharmaceutical "
        "SOP content. Score the QUESTION BATCH quality on a 0.0 to 1.0 scale.\n\n"
        "Rubric (start at 1.0, deduct):\n"
        "- -0.4 if any question is answerable from general knowledge but NOT from the content.\n"
        "- -0.3 if any correct answer is ambiguous or another option is also defensible.\n"
        "- -0.2 if options are implausible, obviously wrong, or duplicate.\n"
        "- -0.2 if questions are vague, leading, or trivial (answer visible in the stem).\n\n"
        "Return ONLY a JSON object: {\"score\": 0.0-1.0, \"reason\": \"<one sentence>\"}\n\n"
        f"CONTENT:\n{content[:2500]}\n\nQUESTIONS:\n{_json.dumps(sample, ensure_ascii=False)[:4000]}\n"
    )
    data = _run_judge(llm, prompt, 'judge-mcq-quality', 'eval_judge_mcq')
    if not isinstance(data, dict) or 'score' not in data:
        return None
    score = max(0.0, min(1.0, float(data['score'])))
    comment = str(data.get('reason', ''))[:500] or None
    if trace_obs is not None:
        score_trace(trace_obs, 'mcq-quality', score, 'NUMERIC', comment)
    else:
        score_current_trace('mcq-quality', score, 'NUMERIC', comment)
    return score


def _run_judge(llm, prompt: str, name: str, operation: str):
    """Run the judge LLM inside an ``evaluator`` observation (traced + costed)."""
    with langfuse_observation(
        name=name,
        as_type='evaluator',
        tags=['eval'],
        metadata={'criteria': 'llm-as-judge', 'model': _JUDGE_MODEL},
    ):
        return llm.generate_json(prompt, user_id=0, operation=operation)
