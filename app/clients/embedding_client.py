import hashlib
import logging

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self):
        from app.core.config import settings
        self._use_real = (
            settings.gemini_api_key
            and settings.gemini_api_key not in ("change-me", "replace-me")
        )
        if self._use_real:
            from google import genai
            self._client = genai.Client(api_key=settings.gemini_api_key)
        else:
            self._client = None
            logger.warning("Gemini API key missing — EmbeddingClient using fallback hash embeddings.")

    def embed_text(self, text: str) -> list[float]:
        if self._use_real and self._client:
            try:
                result = self._client.models.embed_content(
                    model="gemini-embedding-2",
                    contents=text[:8000],  # API limit guard
                )
                return result.embeddings[0].values
            except Exception as e:
                logger.error(f"Real embedding failed, falling back to hash: {e}")
        # Fallback: normalised SHA-256 bytes (768-dim to match real model output shape)
        raw = hashlib.sha256(text.encode("utf-8")).digest()
        # Tile to 768 dims to match text-embedding-004 output size
        base = [round(b / 255.0, 6) for b in raw]
        tiled = (base * ((768 // len(base)) + 1))[:768]
        return tiled

