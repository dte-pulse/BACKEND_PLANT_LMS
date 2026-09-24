APP_VERSION = "0.1.0"

# E-3: single source of truth for the embedding vector dimensionality.
# The DB columns (chunks.embedding, parent_chunks.embedding) use this dimension
# via pgvector Vector(EMBEDDING_DIM). Changing it requires a data migration
# (re-embed every document) — see app/clients/embedding_client.py for the model
# name/version that produced the vectors.
EMBEDDING_DIM = 768


# ── P2 #6 — Per-structure-type retrieval thresholds ───────────────────────────
# Unstructured documents (blind token-batching, no real headings) produce
# fuzzier section boundaries, so their blended retrieval scores sit lower.
# A single global threshold either blocks valid unstructured answers or lets
# noise through for structured SOPs. Structure type is classified once at
# ingest (documents.structure_type) and read at query time.
#
# NOTE (R-1): calibrated via the golden Q/A harness —
#   python -m app.evals.retrieval_eval --calibrate --real
# grid-searches both tables per structure type and prints recommended values.
# Current values are the reasoned defaults pending a real curated golden set.
STRUCTURE_STRUCTURED = 'structured'
STRUCTURE_UNSTRUCTURED = 'unstructured'
STRUCTURE_UNKNOWN = 'unknown'

# Blended-score relevance gate per structure type.
RELEVANCE_THRESHOLD_BY_TYPE = {
    STRUCTURE_STRUCTURED: 0.35,    # unchanged global default
    STRUCTURE_UNSTRUCTURED: 0.25,  # relaxed: fuzzier boundaries score lower
    STRUCTURE_UNKNOWN: 0.35,       # classified = structured by default
}

# Raw-cosine floor for vector-pool chunks per structure type.
MIN_COSINE_FLOOR_BY_TYPE = {
    STRUCTURE_STRUCTURED: 0.15,    # unchanged global default
    STRUCTURE_UNSTRUCTURED: 0.10,  # slightly relaxed, proportionally
    STRUCTURE_UNKNOWN: 0.15,
}
