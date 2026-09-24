# Pulse LMS Backend

## Run locally

```bash
cd Backend
source .venv/bin/activate
python run.py
```

## Background worker

```bash
./scripts/worker.sh
```

## Flower dashboard

```bash
./scripts/flower.sh
```

## Test

```bash
pytest
```

---

## Adaptive learning agents

The adaptive learning loop (dynamic per-user questions, evaluation, weakness
profiling, study plans) is a **multi-agent system** — `CurriculumAgent`,
`QuestionGeneratorAgent`, `EvaluatorAgent`, `WeaknessAgent`,
`RecommenderAgent`, coordinated by `AdaptiveAgentService`. Each agent is
deterministic where possible and falls back gracefully when the LLM is
unavailable.

➡️ See **[`docs/AGENTS.md`](docs/AGENTS.md)** for the full agent catalog, the
shared mastery model, and how the agents work together end-to-end.

---

# Langfuse observability — traces, evals, cost & latency

The whole LLM pipeline is instrumented with [Langfuse](https://langfuse.com) (Python SDK v4):
every LLM call, embedding, retrieval step and adaptive-agent decision is traced with
model, input/output, **latency** and **token usage** (cost is computed from Langfuse's
model pricing table). The integration is a **graceful no-op when unconfigured** — the
app behaves exactly as before.

## 1. Enable it

1. Create a free project at [https://cloud.langfuse.com](https://cloud.langfuse.com) (or self-host).
2. Project → **Settings → API Keys** → create a key pair.
3. Add to `.env`:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or https://us.cloud.langfuse.com
LANGFUSE_ENABLED=true
LANGFUSE_SAMPLE_RATE=1.0        # trace every request; lower for high traffic
LANGFUSE_INGEST_SAMPLE_RATE=0.1 # bulk ingestion → default 10% of documents
LANGFUSE_EVALS_SAMPLE_RATE=0.05 # LLM-as-judge evals are extra Gemini calls → 5%
```

Restart the API (`python run.py`). Traces appear under **Traces** within seconds
(observations export asynchronously).

## 2. What is traced

| Flow | Trace name | Observations |
|---|---|---|
| RAG Q&A (`/learning/session/qa`) | `rag-qa` | `hybrid-retrieval` (retriever) → `rag-answer-generation` (generation) |
| Adaptive question (`/learning/session/child/{id}/question`) | `adaptive-question` | `curriculum-decision`, `question-generator` (agents) → LLM generations |
| Adaptive answer (`/learning/session/child/{id}/answer`) | `adaptive-answer` | `evaluator`, `weakness-update`, `curriculum-next-step` (agents) → LLM generations |
| Document ingestion (Celery) | `ingest-document` | `embed-text` (embedding), `learning-card`, `topic-summary`, `mind-map-structure`, `generate-section-mcqs` (generations) |
| Any standalone LLM call | `<operation>-json/-text` | generation with usage + latency |

Every trace carries `user_id`, tags (`llm`, `qa`, `learning`, `ingest`, …) and the
`feature` metadata, so you can filter and compare per feature, per user, and per document.

## 3. Cost & latency dashboards

- **Cost**: Langfuse computes spend per generation from its model pricing table.
  Usage is read from Gemini's **real `usage_metadata`** (prompt/candidates/cached
  token counts — estimates are only a fallback for mock/no-key paths). Thinking
  tokens (billed at the output rate) are folded into `output` so the reported
  spend matches Google's actual charge. Sent with Langfuse's canonical
  `input`/`output`/`input_cached_tokens` keys, matching the **predefined**
  `gemini-2.5-flash` price definition verbatim
  ($0.30/1M in, $2.50/1M out, $0.03/1M cached read) — LLM cost is exact and
  automatic, no setup needed.
  `gemini-embedding-2` has **no** predefined definition, so embedding cost stays $0
  until you add one custom model in project **Settings → Model pricing**:

  | Field | Value |
  |---|---|
  | Model name (exact) | `gemini-embedding-2` |
  | Usage type (exact) | `input` → **$0.20** / 1M tokens |

  > Langfuse matches usage-type keys **verbatim** against price definitions — the
  > keys must be exactly `input` / `output` (not `input_tokens`), and the model
  > name must match exactly. Cost is computed at ingestion time, so traces created
  > before a definition exists are not backfilled.
- **Latency**: generation duration is captured automatically — filter the **Traces**
  table by `feature:qa` or `feature:adaptive-learning` and sort by duration, or build
  a dashboard with the **Metrics API**.
- Existing cost data (from the `token_usage_logs` table) is unaffected.

## 4. Evals (LLM-as-a-Judge)

Two built-in, code-controlled judges (Gemini judges the app's own output — each judge
call is itself traced as an `evaluator` observation so eval spend is visible):

- `qa-groundedness` (0–1 numeric) — on sampled QA answers: hallucinated / out-of-scope
  answers score low. Filter **Scores** by this name to trend answer quality.
- `mcq-quality` (0–1 numeric) — on a sampled section's generated MCQ batch during ingestion.
- `answer-correctness` (boolean) — attached to **every** adaptive answer (deterministic).

**Recommended production pattern**: Langfuse also supports server-side, observation-level
LLM-as-a-Judge evaluators configured in the UI (**Evaluators** page) with an **LLM
Connection** — set one up to evaluate live observations (e.g. hallucination detection on
`rag-answer-generation`) with zero code changes and independent sampling.

## 5. Tuning knobs

- **Trace sampling is SDK-level** (`LANGFUSE_SAMPLE_RATE` / `sample_rate` in the client
  constructor): Langfuse samples at the trace level, so a sampled-out trace drops
  **all** of its observations and scores together — children never become orphaned
  traces.
- **Ingestion** is additionally sampled at `LANGFUSE_INGEST_SAMPLE_RATE` (default 0.1)
  because bulk ingestion emits hundreds of spans per document. This rate multiplies
  with the global rate (e.g. 0.1 ingest × 0.5 global = 0.05 effective).
- **Evals** run on `LANGFUSE_EVALS_SAMPLE_RATE` (default 0.05) of eligible outputs —
  each judge call is an extra Gemini call, so keep this low.
- Prompts are truncated to 8k chars and outputs to 6k chars in traces; embeddings log
  a vector sample instead of the full 768-dim vector.
- Set `LANGFUSE_ENABLED=false` or remove the keys to fully disable (no network calls).
