"""
Langfuse observability client — traces, evals, cost & latency.

Every LLM call in the app funnels through LLMClient / EmbeddingClient (manual
generations), the adaptive learning agents through AdaptiveAgentService (agent
traces), and retrieval through RagService (retriever observations). This module
wraps the Langfuse Python SDK (v4) with a graceful no-op when unconfigured, so
the application keeps working without keys — matching the codebase's fallback
style (hash embeddings, mock LLM, etc.).

Trace shape (children nest automatically via OpenTelemetry context):

    rag-qa / adaptive-question / adaptive-answer / ingest-document   (root)
      ├─ retriever / agent / evaluator observations
      │    └─ generation observations (LLM calls — usage, latency, cost)
      └─ scores (correctness, qa-groundedness, mcq-quality, ...)

Env vars (see app.core.config.Settings):
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL
    LANGFUSE_SAMPLE_RATE / LANGFUSE_INGEST_SAMPLE_RATE / LANGFUSE_EVALS_SAMPLE_RATE
"""
import contextlib
import contextvars
import logging
import random
import threading

from app.core.config import settings

logger = logging.getLogger(__name__)

_langfuse = None
_initialized = False
_init_lock = threading.Lock()

# When a root observation is manually sampled OUT, this context var tells every
# child observation created inside it to no-op too — otherwise the child would
# create its own root trace (OTel context is empty after the skipped root).
_sampled_out_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    'langfuse_sampled_out', default=False
)

# Set for the duration of every observation that reached the SDK. Used to detect
# the trace root (outermost observation) WITHOUT calling the SDK's
# ``get_current_observation_id`` (which logs a noisy "No active span" warning
# whenever there is no active OTel span, i.e. on every root).
_in_observation_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    'langfuse_in_observation', default=False
)

# Payload truncation — keep traces readable without bloat.
MAX_INPUT_CHARS = 8000
MAX_OUTPUT_CHARS = 6000

class NoopObservation:
    """Stand-in observation when Langfuse is disabled — call sites stay clean."""

    def update(self, *args, **kwargs):  # noqa: D401 - intentional no-op
        pass

    def score(self, *args, **kwargs):
        pass

    def score_trace(self, *args, **kwargs):
        pass

    def end(self, *args, **kwargs):
        pass


_NOOP = NoopObservation()


def langfuse_configured() -> bool:
    """True only when keys are present AND the feature flag is on."""
    return bool(
        settings.langfuse_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def should_sample(rate: float | None) -> bool:
    """Deterministic sample check. None/0 → False, 1.0 → True, else random."""
    if rate is None:
        return False
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


def get_langfuse():
    """Lazy singleton Langfuse client, or None when unconfigured/unavailable.

    Thread-safe: initialisation is guarded by a lock. Trace-level sampling is
    delegated to the SDK (``sample_rate``) so sampled-out traces drop wholesale
    — observations inside them can never become orphaned root traces.
    """
    global _langfuse, _initialized
    if _initialized:
        return _langfuse
    with _init_lock:
        if _initialized:
            return _langfuse
        _initialized = True
        if not langfuse_configured():
            logger.info(
                'Langfuse not configured (set LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL) '
                '— observability, evals and cost tracking disabled.'
            )
            _langfuse = None
            return None
        try:
            # Import AFTER config is loaded so env vars are already set (import
            # order matters for the OTel-based SDK).
            from langfuse import Langfuse
            _langfuse = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url or 'https://cloud.langfuse.com',
                sample_rate=settings.langfuse_sample_rate,
            )
            logger.info('Langfuse client initialised (base_url=%s, sample_rate=%s)',
                        settings.langfuse_base_url, settings.langfuse_sample_rate)
        except Exception as e:  # SDK errors must never break the application
            logger.warning('Langfuse initialisation failed — observability disabled: %s', e)
            _langfuse = None
        return _langfuse


def _propagate_attrs_cm(user_id=None, session_id=None, tags=None, trace_name=None):
    """Build the propagate_attributes context manager, or None when unavailable.

    Kept separate from ``_propagate_attrs`` so that attribute-propagation setup
    failures degrade to a plain yield WITHOUT swallowing exceptions raised in
    the instrumented body. (The old ``except Exception: yield`` pattern caught
    body exceptions and then yielded again after the throw, making contextlib
    raise ``RuntimeError: generator didn't stop after throw()`` — masking the
    real error, e.g. the FK violation during re-ingestion.)
    """
    try:
        from langfuse import propagate_attributes
    except Exception:  # noqa: BLE001 — propagation is best-effort
        return None
    kw = {}
    if user_id is not None:
        kw['user_id'] = str(user_id)
    if session_id is not None:
        kw['session_id'] = str(session_id)
    if tags:
        kw['tags'] = tags
    if trace_name is not None:
        kw['trace_name'] = trace_name
    try:
        return propagate_attributes(**kw)
    except Exception:  # noqa: BLE001 — bad args must never break code
        return None


@contextlib.contextmanager
def _propagate_attrs(user_id=None, session_id=None, tags=None, trace_name=None):
    """Propagate trace attributes (userId/sessionId/tags/traceName) to children.

    user_id / session_id must be strings — the Langfuse SDK silently drops
    non-string values (e.g. the int primary keys this app passes), which breaks
    per-user attribution. trace_name is set only by root observations so that
    child observations carry the trace name and Langfuse's metrics API
    ``traceName`` dimension resolves them (otherwise children appear as
    ``(untraced)`` and the dashboard's per-feature breakdown undercounts).

    Exceptions raised INSIDE the instrumented block always propagate unchanged
    — this wrapper only forwards attributes and never catches body errors.
    """
    if not any([user_id, session_id, tags, trace_name]):
        yield
        return
    cm = _propagate_attrs_cm(user_id, session_id, tags, trace_name)
    if cm is None:
        yield
        return
    with cm:
        yield


@contextlib.contextmanager
def langfuse_observation(*, name: str, as_type: str = 'span', user_id=None,
                         session_id=None, tags=None, metadata=None, model=None,
                         input_data=None, sample_rate: float | None = None):
    """Context manager yielding a Langfuse observation (or a no-op).

    Observation types: span, generation, agent, tool, chain, retriever,
    embedding, evaluator, guardrail, event. Children created inside the ``with``
    block nest automatically (OTel context propagation).

    ``sample_rate`` is for flows the global SDK rate cannot express (e.g. bulk
    ingestion sampled at 10%). When a root with ``sample_rate`` is sampled out,
    a context var suppresses ALL children inside the block so they never become
    orphaned root traces.
    """
    lf = get_langfuse()
    if lf is None:
        yield _NOOP
        return
    if sample_rate is not None and not should_sample(sample_rate):
        token = _sampled_out_ctx.set(True)
        try:
            yield _NOOP
        finally:
            _sampled_out_ctx.reset(token)
        return
    if _sampled_out_ctx.get():
        yield _NOOP
        return
    kw = {}
    if model:
        kw['model'] = model
    if input_data is not None:
        kw['input'] = input_data
    if metadata is not None:
        kw['metadata'] = metadata
    # Root = the outermost observation (no parent langfuse_observation active).
    # Tracked with our own context var — the SDK's ``get_current_observation_id``
    # warns on every root ("No active span") and is unreliable inside the block.
    is_root = not _in_observation_ctx.get()
    token = _in_observation_ctx.set(True)
    try:
        with lf.start_as_current_observation(as_type=as_type, name=name, **kw) as obs:
            # Roots propagate their name as trace_name so children inherit it
            # (Langfuse's metrics traceName dimension resolves per-observation).
            with _propagate_attrs(
                user_id=user_id, session_id=session_id, tags=tags,
                trace_name=(name if is_root else None),
            ):
                yield obs
    finally:
        _in_observation_ctx.reset(token)


def score_trace(obs, name: str, value, data_type: str = 'NUMERIC', comment: str | None = None):
    """Attach a score to a trace from an observation object (no-op when disabled)."""
    try:
        obs.score_trace(name=name, value=value, data_type=data_type, comment=comment)
    except Exception:  # noqa: BLE001 — scoring must never break the request
        pass


def score_observation(obs, name: str, value, data_type: str = 'NUMERIC', comment: str | None = None):
    """Attach a score to the observation itself (no-op when disabled)."""
    try:
        obs.score(name=name, value=value, data_type=data_type, comment=comment)
    except Exception:  # noqa: BLE001
        pass


def score_current_trace(name: str, value, data_type: str = 'NUMERIC', comment: str | None = None):
    """Score the currently active trace (usable inside any instrumented block)."""
    lf = get_langfuse()
    if lf is None:
        return
    try:
        lf.score_current_trace(name=name, value=value, data_type=data_type, comment=comment)
    except Exception:  # noqa: BLE001
        pass


def flush_langfuse(timeout: float = 5.0) -> bool:
    """Export queued events (call before process exit / shutdown). Bounded.

    The OTLP exporter inside ``lf.flush()`` can block for a long time on
    network issues — observed as intermittent TestClient teardown hangs. Run
    the flush in a daemon thread and give up after ``timeout`` seconds: a
    dropped batch is acceptable, a hung shutdown is not.
    """
    lf = get_langfuse()
    if lf is None:
        return True
    done = threading.Event()

    def _run():
        try:
            lf.flush()
        except Exception:  # noqa: BLE001
            pass
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True, name='langfuse-flush').start()
    return done.wait(timeout)


def shutdown_langfuse(timeout: float = 5.0) -> bool:
    """Stop the background exporter thread (app shutdown). Bounded like flush.

    Returns True when the exporter stopped within ``timeout``; the thread is
    daemon so a straggler never blocks process exit.
    """
    lf = get_langfuse()
    if lf is None:
        return True
    done = threading.Event()

    def _run():
        try:
            lf.shutdown()
        except Exception:  # noqa: BLE001
            pass
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True, name='langfuse-shutdown').start()
    return done.wait(timeout)
