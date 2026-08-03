"""migrate embeddings from JSON to pgvector vector(768)

Revision ID: a1b2c3d4e5f6
Revises: 586a40967923
Create Date: 2026-08-01

This migration:
1. Enables the pgvector extension in Postgres
2. Converts chunks.embedding from JSON -> vector(768)
3. Converts parent_chunks.embedding from JSON -> vector(768)
4. Creates HNSW indexes on both tables for fast cosine similarity search

NOTE: Existing JSON embedding data will be cast to vector.
      Rows with NULL or malformed embeddings remain NULL.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers
revision = 'a1b2c3d4e5f6'
down_revision = '586a40967923'
branch_labels = None
depends_on = None

VECTOR_DIM = 768


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Enable pgvector extension (idempotent)
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    # 2. Migrate chunks.embedding: JSON -> vector(768)
    # Add a new vector column, populate from JSON cast, then swap
    conn.execute(sa.text(
        "ALTER TABLE chunks ADD COLUMN embedding_vec vector(768)"
    ))
    conn.execute(sa.text("""
        UPDATE chunks
        SET embedding_vec = (
            CASE
                WHEN embedding IS NOT NULL
                     AND json_array_length(embedding) = 768
                THEN (
                    SELECT array_agg(v::float4)::vector
                    FROM json_array_elements_text(embedding) AS v
                )
                ELSE NULL
            END
        )
    """))
    conn.execute(sa.text("ALTER TABLE chunks DROP COLUMN embedding"))
    conn.execute(sa.text("ALTER TABLE chunks RENAME COLUMN embedding_vec TO embedding"))

    # 3. Migrate parent_chunks.embedding: JSON -> vector(768)
    conn.execute(sa.text(
        "ALTER TABLE parent_chunks ADD COLUMN embedding_vec vector(768)"
    ))
    conn.execute(sa.text("""
        UPDATE parent_chunks
        SET embedding_vec = (
            CASE
                WHEN embedding IS NOT NULL
                     AND json_array_length(embedding) = 768
                THEN (
                    SELECT array_agg(v::float4)::vector
                    FROM json_array_elements_text(embedding) AS v
                )
                ELSE NULL
            END
        )
    """))
    conn.execute(sa.text("ALTER TABLE parent_chunks DROP COLUMN embedding"))
    conn.execute(sa.text("ALTER TABLE parent_chunks RENAME COLUMN embedding_vec TO embedding"))

    # 4. Create HNSW indexes for fast cosine similarity search
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
        ON chunks USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_parent_chunks_embedding_hnsw
        ON parent_chunks USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Drop indexes
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_parent_chunks_embedding_hnsw"))

    # Revert chunks.embedding: vector -> JSON
    conn.execute(sa.text(
        "ALTER TABLE chunks ADD COLUMN embedding_json json"
    ))
    conn.execute(sa.text("""
        UPDATE chunks
        SET embedding_json = to_json(embedding::float4[])
        WHERE embedding IS NOT NULL
    """))
    conn.execute(sa.text("ALTER TABLE chunks DROP COLUMN embedding"))
    conn.execute(sa.text("ALTER TABLE chunks RENAME COLUMN embedding_json TO embedding"))

    # Revert parent_chunks.embedding: vector -> JSON
    conn.execute(sa.text(
        "ALTER TABLE parent_chunks ADD COLUMN embedding_json json"
    ))
    conn.execute(sa.text("""
        UPDATE parent_chunks
        SET embedding_json = to_json(embedding::float4[])
        WHERE embedding IS NOT NULL
    """))
    conn.execute(sa.text("ALTER TABLE parent_chunks DROP COLUMN embedding"))
    conn.execute(sa.text("ALTER TABLE parent_chunks RENAME COLUMN embedding_json TO embedding"))
