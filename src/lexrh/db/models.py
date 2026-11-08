"""Legal-corpus tables: one row per article (documents) and per embedded chunk."""

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from lexrh.config import EMBEDDING_DIM


class Base(DeclarativeBase):
    pass


class Document(Base):
    """A legal text (an article of the Code du travail or a collective agreement)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(8))  # "LEGI" or "KALI"
    legal_id: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. LEGIARTI000...
    title: Mapped[str] = mapped_column(Text)
    idcc: Mapped[str | None] = mapped_column(String(8), nullable=True)
    valid_from: Mapped[date] = mapped_column()
    valid_to: Mapped[date | None] = mapped_column(nullable=True)  # NULL = still in force
    status: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Parsed article content (added in migration 0002). Chunks are built from these.
    num: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. "L1234-1"
    text_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    hierarchy: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    nota: Mapped[str] = mapped_column(Text, default="", server_default="")

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete")

    __table_args__ = (
        Index("ix_documents_idcc_validity", "idcc", "valid_from", "valid_to"),
        Index("ix_documents_text_num_from", "text_id", "num", "valid_from"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ord: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('french', text)", persisted=True)
    )
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )
