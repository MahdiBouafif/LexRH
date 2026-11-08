"""Chunk every stored article, embed the chunks, and store them.

Idempotent: a document is processed only when it has no chunk built from its
CURRENT ``content_hash``. A second run does nothing and costs nothing. When an
article changes (new hash), its old chunks are replaced.

Commands (see the Makefile):
    python -m lexrh.rag.embed_chunks --dry-run     # count tokens and cost, call nothing
    python -m lexrh.rag.embed_chunks               # chunk, embed, store
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, delete, exists, select
from sqlalchemy.orm import Session

from lexrh.config import get_settings
from lexrh.db.models import Chunk as ChunkRow
from lexrh.db.models import Document
from lexrh.db.session import get_engine
from lexrh.rag.chunking import DEFAULT_MAX_TOKENS, Chunk, chunk_article, estimate_tokens
from lexrh.rag.embeddings import Embedder, MistralEmbedder

logger = logging.getLogger(__name__)

DEFAULT_DOC_BATCH = 64  # documents loaded and committed together
MAX_TEXTS_PER_CALL = 32  # texts per API request
MAX_TOKENS_PER_CALL = 12_000  # estimated tokens per API request (pessimistic estimate)
DEFAULT_PRICE_PER_MILLION = 0.10  # USD. ASSUMPTION: check your Mistral console price.


@dataclass
class EmbedStats:
    documents: int = 0
    chunks: int = 0
    estimated_tokens: int = 0
    billed_tokens: int = 0  # reported by the API (real run only)
    multi_chunk_documents: int = 0
    largest_chunk_tokens: int = 0
    api_calls: int = 0
    biggest_documents: list[tuple[str, int]] = field(default_factory=list)  # (num, chunks)


def _has_current_chunks() -> Any:
    """SQL condition: this document already has a chunk built from its current hash."""
    meta: Any = ChunkRow.meta
    return exists().where(
        ChunkRow.document_id == Document.id,
        meta["content_hash"].as_string() == Document.content_hash,
    )


def _pending_batch(
    session: Session, after: uuid.UUID | None, size: int, text_id: str | None
) -> list[Document]:
    """Next ``size`` documents (ordered by id) that still need chunks."""
    query = select(Document).where(~_has_current_chunks()).order_by(Document.id).limit(size)
    if after is not None:
        query = query.where(Document.id > after)
    if text_id:
        query = query.where(Document.text_id == text_id)
    return list(session.scalars(query))


def _chunks_of(document: Document, max_tokens: int) -> list[Chunk]:
    return chunk_article(
        num=document.num,
        hierarchy=document.hierarchy,
        text=document.text,
        nota=document.nota,
        max_tokens=max_tokens,
    )


def _record(stats: EmbedStats, document: Document, chunks: Sequence[Chunk]) -> None:
    stats.documents += 1
    stats.chunks += len(chunks)
    for chunk in chunks:
        stats.estimated_tokens += chunk.token_estimate
        stats.largest_chunk_tokens = max(stats.largest_chunk_tokens, chunk.token_estimate)
    if len(chunks) > 1:
        stats.multi_chunk_documents += 1
        stats.biggest_documents.append((document.num or document.legal_id, len(chunks)))
        stats.biggest_documents.sort(key=lambda item: item[1], reverse=True)
        del stats.biggest_documents[5:]


def _call_groups(texts: list[str]) -> list[list[int]]:
    """Group text indexes into API calls bounded by count and estimated tokens."""
    groups: list[list[int]] = []
    current: list[int] = []
    tokens = 0
    for index, text in enumerate(texts):
        cost = estimate_tokens(text)
        if current and (len(current) >= MAX_TEXTS_PER_CALL or tokens + cost > MAX_TOKENS_PER_CALL):
            groups.append(current)
            current, tokens = [], 0
        current.append(index)
        tokens += cost
    if current:
        groups.append(current)
    return groups


def _embed_texts(embedder: Embedder, texts: list[str], stats: EmbedStats) -> list[list[float]]:
    vectors: list[list[float]] = [[] for _ in texts]
    for group in _call_groups(texts):
        result = embedder.embed([texts[i] for i in group])
        stats.api_calls += 1
        stats.billed_tokens += result.tokens
        for position, vector in zip(group, result.vectors, strict=True):
            vectors[position] = vector
    return vectors


def dry_run(
    engine: Engine,
    *,
    text_id: str | None = None,
    limit: int | None = None,
    doc_batch: int = DEFAULT_DOC_BATCH,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> EmbedStats:
    """Chunk what is pending and report sizes. Writes nothing, calls no API."""
    stats = EmbedStats()
    after: uuid.UUID | None = None
    with Session(engine) as session:
        while limit is None or stats.documents < limit:
            batch = _pending_batch(session, after, doc_batch, text_id)
            if not batch:
                break
            for document in batch:
                if limit is not None and stats.documents >= limit:
                    break
                _record(stats, document, _chunks_of(document, max_tokens))
            after = batch[-1].id
    return stats


def embed_pending(
    engine: Engine,
    embedder: Embedder,
    *,
    text_id: str | None = None,
    limit: int | None = None,
    doc_batch: int = DEFAULT_DOC_BATCH,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> EmbedStats:
    """Chunk, embed and store every pending document. Commits after each document batch."""
    stats = EmbedStats()
    after: uuid.UUID | None = None
    with Session(engine) as session:
        while limit is None or stats.documents < limit:
            batch = _pending_batch(session, after, doc_batch, text_id)
            if not batch:
                break
            if limit is not None:
                batch = batch[: limit - stats.documents]

            per_document = [(document, _chunks_of(document, max_tokens)) for document in batch]
            texts = [chunk.text for _, chunks in per_document for chunk in chunks]
            vectors = _embed_texts(embedder, texts, stats)

            # Replace old chunks (document changed) and insert the new ones, atomically.
            session.execute(delete(ChunkRow).where(ChunkRow.document_id.in_([d.id for d in batch])))
            position = 0
            for document, chunks in per_document:
                _record(stats, document, chunks)
                for chunk in chunks:
                    session.add(
                        ChunkRow(
                            document_id=document.id,
                            ord=chunk.ord,
                            text=chunk.text,
                            embedding=vectors[position],
                            meta={"content_hash": document.content_hash},
                        )
                    )
                    position += 1
            session.commit()
            after = batch[-1].id
            logger.info("embedded %d documents, %d chunks", stats.documents, stats.chunks)
    return stats


def format_report(stats: EmbedStats, *, wrote: bool, price_per_million: float) -> str:
    tokens = stats.billed_tokens if wrote and stats.billed_tokens else stats.estimated_tokens
    label = "billed tokens" if wrote and stats.billed_tokens else "estimated tokens (pessimistic)"
    cost = tokens / 1_000_000 * price_per_million
    lines = [
        "",
        f"documents to process:      {stats.documents}",
        f"chunks:                    {stats.chunks}",
        f"documents split in >1:     {stats.multi_chunk_documents}",
        f"largest chunk (est. tok):  {stats.largest_chunk_tokens}",
        f"{label + ':':<27}{tokens}",
        f"cost at ${price_per_million}/M tokens:  ${cost:.2f}   (check the price in your console)",
    ]
    if wrote:
        lines.append(f"API calls:                 {stats.api_calls}")
    if stats.biggest_documents:
        lines.append(
            "most chunks per article:   "
            + ", ".join(f"{num} ({count})" for num, count in stats.biggest_documents)
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunk and embed stored legal articles.")
    parser.add_argument("--dry-run", action="store_true", help="count only, no API call, no write")
    parser.add_argument("--limit", type=int, default=None, help="process at most N documents")
    parser.add_argument("--doc-batch", type=int, default=DEFAULT_DOC_BATCH)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--price-per-million", type=float, default=DEFAULT_PRICE_PER_MILLION)
    parser.add_argument("--text-id", default=None, help="only this LEGITEXT (default: all)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=get_settings().log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    engine = get_engine()

    if args.dry_run:
        stats = dry_run(
            engine,
            text_id=args.text_id,
            limit=args.limit,
            doc_batch=args.doc_batch,
            max_tokens=args.max_tokens,
        )
        print(format_report(stats, wrote=False, price_per_million=args.price_per_million))
        return 0

    embedder = MistralEmbedder(get_settings().mistral_api_key)
    stats = embed_pending(
        engine,
        embedder,
        text_id=args.text_id,
        limit=args.limit,
        doc_batch=args.doc_batch,
        max_tokens=args.max_tokens,
    )
    print(format_report(stats, wrote=True, price_per_million=args.price_per_million))
    return 0


if __name__ == "__main__":
    sys.exit(main())
