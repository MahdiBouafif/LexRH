"""Hybrid retrieval: "which articles answer this question, as the law stood on date X?"

Pipeline
    1. Date filter (in SQL, applied to BOTH searches):
         valid_from <= as_of < valid_to   (end date exclusive, NULL = still in force)
         status != 'ANNULE'               (annulled texts are treated as never in force)
         one version per article          (if versions overlap, the latest valid_from wins)
    2. Vector search: nearest chunks by cosine distance (HNSW index).
    3. Keyword search: French full-text, any query word may match (OR), ranked by ts_rank_cd.
    4. Reciprocal Rank Fusion of both rankings, then one chunk per article, top k.

Why hybrid: vectors find paraphrases ("rupture du contrat" ~ "licenciement"); keywords find
exact legal terms and article numbers that embeddings blur. Fusing ranks (RRF) avoids
comparing two scores that live on different scales.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from lexrh.config import get_settings
from lexrh.db.session import get_engine
from lexrh.rag.embeddings import Embedder, MistralEmbedder
from lexrh.rag.ingest import CODE_DU_TRAVAIL

logger = logging.getLogger(__name__)

RRF_K = 60  # standard constant from the original RRF paper; damps the weight of top ranks
CANDIDATES = 50  # per search, before fusion
DEFAULT_TOP_K = 5
EF_SEARCH = 100  # HNSW: how many neighbours are explored (default 40); higher = better recall

# Documents valid on :as_of, one version per article. Alias `d` is the documents table.
VALID_DOCUMENT = """
    d.text_id = :text_id
    AND d.status <> 'ANNULE'
    AND d.valid_from <= :as_of
    AND (d.valid_to IS NULL OR d.valid_to > :as_of)
    AND NOT EXISTS (
        SELECT 1 FROM documents n
        WHERE n.text_id = d.text_id AND n.num = d.num
          AND n.status <> 'ANNULE'
          AND n.valid_from <= :as_of
          AND (n.valid_to IS NULL OR n.valid_to > :as_of)
          AND (n.valid_from, n.id) > (d.valid_from, d.id)
    )
"""

VECTOR_SQL = f"""
    SELECT c.id
    FROM chunks c JOIN documents d ON d.id = c.document_id
    WHERE {VALID_DOCUMENT}
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :n
"""  # noqa: S608 (constant fragments, user values are bound parameters)

KEYWORD_SQL = f"""
    SELECT c.id
    FROM chunks c JOIN documents d ON d.id = c.document_id,
         to_tsquery('french', :tsq) q
    WHERE c.tsv @@ q AND {VALID_DOCUMENT}
    ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id
    LIMIT :n
"""  # noqa: S608

DETAILS_SQL = """
    SELECT c.id, c.ord, c.text AS chunk_text, d.id AS document_id, d.num, d.legal_id,
           d.valid_from, d.valid_to, d.status, d.hierarchy, d.nota
    FROM chunks c JOIN documents d ON d.id = c.document_id
    WHERE c.id = ANY(:ids)
"""

_WORD = re.compile(r"\w{3,}", re.UNICODE)


def keyword_query(question: str) -> str | None:
    """Turn a natural-language question into an OR tsquery: 'a | b | c'.

    A user question is not a list of required terms, so AND (the default of plainto_tsquery)
    returns nothing for most sentences. Only word characters survive, which also makes the
    string safe for to_tsquery. Stop words are dropped later by the French dictionary.
    """
    words = list(dict.fromkeys(word.lower() for word in _WORD.findall(question)))
    return " | ".join(words) if words else None


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[uuid.UUID]], k: int = RRF_K
) -> list[tuple[uuid.UUID, float]]:
    """Merge several ranked lists. score(item) = sum over lists of 1 / (k + rank), rank from 1."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], str(pair[0])))


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    num: str | None
    legal_id: str
    valid_from: date
    valid_to: date | None
    status: str
    hierarchy: list[str]
    nota: str
    text: str
    score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None
    ranks: dict[str, int] = field(default_factory=dict)


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def _set_search_parameters(session: Session) -> None:
    """Raise HNSW recall; use iterative scan when this pgvector supports it (0.8+).

    With a selective WHERE (a date filter keeps ~1/4 of rows), HNSW can return fewer than
    LIMIT rows because filtering happens after the index scan. Iterative scan keeps scanning
    until enough rows pass. Each SET sits in a savepoint so an older pgvector cannot break
    the request.
    """
    for statement in (
        f"SET LOCAL hnsw.ef_search = {EF_SEARCH}",
        "SET LOCAL hnsw.iterative_scan = relaxed_order",
    ):
        nested = session.begin_nested()
        try:
            session.execute(text(statement))
            nested.commit()
        except Exception:  # unknown parameter on an older pgvector
            nested.rollback()
            logger.debug("search parameter not supported: %s", statement)


def search(
    engine: Engine,
    embedder: Embedder,
    question: str,
    as_of: date,
    *,
    k: int = DEFAULT_TOP_K,
    text_id: str = CODE_DU_TRAVAIL,
    candidates: int = CANDIDATES,
) -> list[SearchHit]:
    """Return the top ``k`` articles for ``question``, as in force on ``as_of``."""
    question = question.strip()
    if not question:
        return []

    query_vector = embedder.embed([question]).vectors[0]
    params: dict[str, Any] = {"text_id": text_id, "as_of": as_of, "n": candidates}

    with Session(engine) as session:
        _set_search_parameters(session)
        vector_ids = list(
            session.execute(
                text(VECTOR_SQL), {**params, "qvec": _vector_literal(query_vector)}
            ).scalars()
        )
        tsquery = keyword_query(question)
        keyword_ids = (
            list(session.execute(text(KEYWORD_SQL), {**params, "tsq": tsquery}).scalars())
            if tsquery
            else []
        )

        fused = reciprocal_rank_fusion([vector_ids, keyword_ids])
        if not fused:
            return []
        rows = {
            row.id: row
            for row in session.execute(text(DETAILS_SQL), {"ids": [cid for cid, _ in fused]})
        }

    vector_rank = {cid: rank for rank, cid in enumerate(vector_ids, start=1)}
    keyword_rank = {cid: rank for rank, cid in enumerate(keyword_ids, start=1)}

    hits: list[SearchHit] = []
    seen_documents: set[uuid.UUID] = set()
    for chunk_id, score in fused:  # best chunk first, so the first one per article wins
        row = rows[chunk_id]
        if row.document_id in seen_documents:
            continue
        seen_documents.add(row.document_id)
        hits.append(
            SearchHit(
                chunk_id=chunk_id,
                document_id=row.document_id,
                num=row.num,
                legal_id=row.legal_id,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                status=row.status,
                hierarchy=list(row.hierarchy),
                nota=row.nota,
                text=row.chunk_text,
                score=score,
                vector_rank=vector_rank.get(chunk_id),
                keyword_rank=keyword_rank.get(chunk_id),
            )
        )
        if len(hits) == k:
            break
    return hits


def format_hits(hits: Sequence[SearchHit], as_of: date) -> str:
    if not hits:
        return f"No article found as of {as_of}."
    lines = [f"Results as of {as_of}:", ""]
    for position, hit in enumerate(hits, start=1):
        validity = f"{hit.valid_from} -> {hit.valid_to or 'now'}"
        ranks = f"vector #{hit.vector_rank or '-'}, keyword #{hit.keyword_rank or '-'}"
        preview = hit.text.replace("\n", " ")[:220]
        lines.append(f"{position}. Article {hit.num}  [{hit.status}, {validity}]  ({ranks})")
        lines.append(f"   score {hit.score:.4f}  {preview}...")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search the legal corpus as of a date.")
    parser.add_argument("question")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--text-id", default=CODE_DU_TRAVAIL)
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.date) if args.date else date.today()
    embedder = MistralEmbedder(get_settings().mistral_api_key)
    hits = search(get_engine(), embedder, args.question, as_of, k=args.k, text_id=args.text_id)
    print(format_hits(hits, as_of))
    return 0


if __name__ == "__main__":
    sys.exit(main())
