import hashlib
import logging
import time

from app.utils.constants import EMBEDDING_DIM

logger = logging.getLogger(__name__)

# ── Embedding model version guard ─────────────────────────────────────────────
# Bump this string whenever the model name OR output_dimensionality changes.
# Any vector stored under a different version is incompatible and must be
# re-embedded before it can be used for cosine similarity.
EMBEDDING_MODEL_NAME = "gemini-embedding-2"
EMBEDDING_MODEL_VERSION = f"{EMBEDDING_MODEL_NAME}-dim{EMBEDDING_DIM}"

# Version tag used for the dev/no-key hash fallback. Stored on documents so the
# retrieval layer can detect that vectors are hash-based and refuse to run
# pgvector cosine search against them.
HASH_EMBEDDING_VERSION = "hash-fallback-v1"

# Transient-failure retry: attempt the API call up to 3 times with backoff
# before surfacing the failure.
_MAX_EMBED_RETRIES = 3
_RETRY_BASE_DELAY_S = 1.0


class EmbeddingError(Exception):
    """Raised when real embeddings cannot be produced.

    Raised instead of silently falling back to hash vectors, because mixing
    hash vectors with real Gemini vectors in the same column makes cosine
    similarity retrieval meaningless (E-1 fix).
    """


class EmbeddingClient:
    def __init__(self):
        from app.core.config import settings
        self._use_real = (
            settings.gemini_api_key
            and settings.gemini_api_key not in ("change-me", "replace-me")
        )
        if self._use_real:
            from google import genai
            from google.genai import types
            # Bound the call (30s) so a slow/hung embedding API can't stall the
            # request — callers surface EmbeddingError and degrade gracefully.
            self._client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=30_000),
            )
        else:
            self._client = None
            logger.warning("Gemini API key missing — EmbeddingClient in HASH fallback mode (dev only).")
        logger.debug(
            f"EmbeddingClient initialised: model_version={self.get_model_version()}, "
            f"use_real={self._use_real}"
        )

    @staticmethod
    def get_model_version() -> str:
        """Return the current embedding model version string.
        Compare against a stored version tag before executing cosine similarity
        queries to detect stale, incompatible vectors in the database."""
        return EMBEDDING_MODEL_VERSION

    @staticmethod
    def get_hash_version() -> str:
        """Version tag for the dev/no-key hash fallback vectors."""
        return HASH_EMBEDDING_VERSION

    @classmethod
    def resolve_versions(cls, client) -> tuple[str, str]:
        """Resolve (model_version, hash_version) for ANY embedding client,

        real or duck-typed. Prefers CALLABLE per-instance overrides (offline
        stubs, test doubles that genuinely produce different vectors must be
        able to re-tag these), falls back to the canonical class staticmethods
        for minimal fakes that only carry ``use_real`` + ``embed_text``. This
        replaces the previous class-level EmbeddingClient.get_model_version()
        call that ignored instance overrides entirely (E-2 regression found by
        the offline E2E: stub-embedded docs were tagged with the real-model
        version and then refused at query time).
        """
        model_fn = getattr(client, 'get_model_version', None)
        hash_fn = getattr(client, 'get_hash_version', None)
        if not callable(model_fn):
            model_fn = cls.get_model_version
        if not callable(hash_fn):
            hash_fn = cls.get_hash_version
        return model_fn(), hash_fn()

    @property
    def use_real(self) -> bool:
        """True when real Gemini embeddings are enabled (API key configured)."""
        return self._use_real

    def embed_text(self, text: str) -> list[float]:
        """Embed ``text`` using the configured model.

        Raises EmbeddingError when real embeddings are enabled but the API call
        fails after retries — callers must surface this (e.g. mark the document
        failed) rather than persist incompatible hash vectors.
        """
        if self._use_real and self._client:
            return self._embed_real(text)
        # No API key: explicit dev-mode hash fallback. This is safe only
        # because documents are tagged with HASH_EMBEDDING_VERSION and the
        # retrieval layer refuses to mix hash and real vectors (E-2).
        return self._hash_embed(text)

    def _embed_real(self, text: str) -> list[float]:
        from google.genai import types
        from app.clients.langfuse_client import langfuse_observation

        last_err: Exception | None = None
        with langfuse_observation(
            name='embed-text',
            as_type='embedding',
            model=EMBEDDING_MODEL_NAME,
            input_data={'text': text[:2000]},
            metadata={'dimensions': EMBEDDING_DIM, 'model_version': EMBEDDING_MODEL_VERSION, 'operation': 'embedding'},
        ) as obs:
            for attempt in range(1, _MAX_EMBED_RETRIES + 1):
                try:
                    result = self._client.models.embed_content(
                        model=EMBEDDING_MODEL_NAME,
                        contents=text[:8000],  # API limit guard
                        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
                    )
                    vec = result.embeddings[0].values
                    from app.utils.tokenizer import estimate_tokens
                    # Real token count from the embed response when available
                    # (embedding models report prompt/total token counts).
                    um = getattr(result, 'usage_metadata', None)
                    input_tokens = 0
                    if um is not None:
                        input_tokens = (
                            int(getattr(um, 'prompt_token_count', 0) or 0)
                            or int(getattr(um, 'total_token_count', 0) or 0)
                        )
                    if not input_tokens:
                        input_tokens = estimate_tokens(text)
                    obs.update(
                        output={'dimensions': len(vec), 'vector_sample': vec[:8]},
                        usage_details={'input': input_tokens},
                    )
                    return vec
                except Exception as e:
                    last_err = e
                    if attempt < _MAX_EMBED_RETRIES:
                        delay = _RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                        logger.warning(
                            f"Real embedding attempt {attempt}/{_MAX_EMBED_RETRIES} failed ({e}); "
                            f"retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
            obs.update(level='ERROR', status_message=str(last_err))
        logger.error(
            f"Real embedding failed after {_MAX_EMBED_RETRIES} attempts: {last_err} "
            f"— refusing to fall back to hash vectors (E-1)"
        )
        raise EmbeddingError(f"Real embedding failed after {_MAX_EMBED_RETRIES} attempts: {last_err}") from last_err

    def _hash_embed(self, text: str) -> list[float]:
        """Normalised SHA-256 bytes tiled to EMBEDDING_DIM — dev-only fallback."""
        raw = hashlib.sha256(text.encode("utf-8")).digest()
        base = [round(b / 255.0, 6) for b in raw]
        tiled = (base * ((EMBEDDING_DIM // len(base)) + 1))[:EMBEDDING_DIM]
        return tiled
