"""Load LEGI article XML files into the `documents` table.

Usage (from the repo root):

    # 1. Look first: parse everything, write nothing, print a report.
    uv run python -m lexrh.rag.ingest data/raw/LEGITEXT000006072050 --dry-run

    # 2. Then load. Running it again is safe: unchanged articles are left alone.
    uv run python -m lexrh.rag.ingest data/raw/LEGITEXT000006072050

Rules, in this order, for each file:
  1. cannot be parsed             -> counted as failed, first examples logged
  2. belongs to another text      -> skipped (only the Code du travail by default)
  3. no real start date / no text -> skipped (not retrievable, see parser.py)
  4. otherwise                    -> upserted by legal_id, only if content_hash changed
"""

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, Table, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexrh.db.models import Document
from lexrh.db.session import get_engine
from lexrh.rag.parser import Article, parse_article_file

CODE_DU_TRAVAIL = "LEGITEXT000006072050"
MAX_FAILURE_EXAMPLES = 20

logger = logging.getLogger("lexrh.ingest")


@dataclass
class Stats:
    seen: int = 0
    failed: int = 0
    skipped_other_text: int = 0
    skipped_not_indexable: int = 0
    ready: int = 0  # rows that passed every check
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    etat_counts: Counter[str] = field(default_factory=Counter)
    failures: list[tuple[str, str]] = field(default_factory=list)


def article_to_row(article: Article) -> dict[str, Any]:
    """Map a parsed article to a `documents` row."""
    label = article.text_title or "LEGI"
    title = f"{label} - art. {article.num}" if article.num else f"{label} - {article.legal_id}"
    return {
        "source": "LEGI",
        "legal_id": article.legal_id,
        "title": title,
        "idcc": None,
        "valid_from": article.valid_from,
        "valid_to": article.valid_to,
        "status": article.etat or "UNKNOWN",
        "content_hash": article.content_hash,
        "num": article.num,
        "text_id": article.text_id,
        "hierarchy": list(article.hierarchy),
        "text": article.text,
        "nota": article.nota,
    }


def scan(
    root: Path, stats: Stats, text_id: str | None = CODE_DU_TRAVAIL
) -> Iterator[dict[str, Any]]:
    """Walk `root`, parse every article file, yield rows that should be stored."""
    for path in root.rglob("LEGIARTI*.xml"):
        stats.seen += 1
        if stats.seen % 10_000 == 0:
            logger.info("scanned %d files", stats.seen)
        try:
            article = parse_article_file(path)
        except (OSError, ValueError) as exc:  # ParseError and pydantic errors are ValueErrors
            stats.failed += 1
            if len(stats.failures) < MAX_FAILURE_EXAMPLES:
                stats.failures.append((str(path), str(exc)))
            continue
        stats.etat_counts[article.etat or "(empty)"] += 1
        if text_id is not None and article.text_id != text_id:
            stats.skipped_other_text += 1
            continue
        if not article.is_indexable:
            stats.skipped_not_indexable += 1
            continue
        stats.ready += 1
        yield article_to_row(article)


def upsert_batch(session: Session, rows: list[dict[str, Any]], stats: Stats) -> None:
    """Insert new articles, update changed ones, leave identical ones untouched."""
    unique = {row["legal_id"]: row for row in rows}  # one row per id inside a statement
    table = cast(Table, Document.__table__)
    base = insert(table)
    upsert = base.on_conflict_do_update(
        index_elements=["legal_id"],
        set_={
            column: base.excluded[column]
            for column in (
                "title",
                "valid_from",
                "valid_to",
                "status",
                "content_hash",
                "num",
                "text_id",
                "hierarchy",
                "text",
                "nota",
            )
        },
        where=table.c.content_hash.is_distinct_from(base.excluded.content_hash),
    )
    statement: Any = upsert.returning(literal_column("(xmax = 0)").label("was_inserted"))
    flags = session.execute(statement, list(unique.values())).scalars().all()
    inserted = sum(1 for flag in flags if flag)
    stats.inserted += inserted
    stats.updated += len(flags) - inserted
    stats.unchanged += len(unique) - len(flags)


def _batches(rows: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def ingest(
    root: Path,
    engine: Engine,
    *,
    text_id: str | None = CODE_DU_TRAVAIL,
    batch_size: int = 1000,
    limit: int | None = None,
) -> Stats:
    stats = Stats()
    rows = scan(root, stats, text_id)
    if limit is not None:
        rows = (row for _, row in zip(range(limit), rows, strict=False))
    with Session(engine) as session:
        for batch in _batches(rows, batch_size):
            upsert_batch(session, batch, stats)
            session.commit()
            logger.info("stored %d articles so far", stats.inserted + stats.updated)
    return stats


def dry_run(root: Path, *, text_id: str | None = CODE_DU_TRAVAIL) -> Stats:
    stats = Stats()
    for _ in scan(root, stats, text_id):
        pass
    return stats


def format_report(stats: Stats, *, wrote: bool) -> str:
    lines = [
        "",
        f"files seen:                {stats.seen}",
        f"failed to parse:           {stats.failed}",
        f"skipped (other text):      {stats.skipped_other_text}",
        f"skipped (not indexable):   {stats.skipped_not_indexable}",
        f"ready to store:            {stats.ready}",
    ]
    if wrote:
        lines += [
            f"  inserted:                {stats.inserted}",
            f"  updated:                 {stats.updated}",
            f"  unchanged:               {stats.unchanged}",
        ]
    lines.append("")
    lines.append("ETAT values seen:")
    for etat, count in stats.etat_counts.most_common():
        lines.append(f"  {etat:<18} {count}")
    if stats.failures:
        lines.append("")
        lines.append(f"first {len(stats.failures)} failures:")
        for path, message in stats.failures:
            lines.append(f"  {path}: {message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load LEGI article XML into PostgreSQL.")
    parser.add_argument("folder", type=Path, help="folder containing LEGIARTI*.xml files")
    parser.add_argument("--dry-run", action="store_true", help="parse only, write nothing")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=None, help="store at most N articles")
    parser.add_argument(
        "--text-id",
        default=CODE_DU_TRAVAIL,
        help="only keep articles of this LEGITEXT (default: Code du travail); "
        "use 'any' to keep everything",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.folder.is_dir():
        print(f"Not a folder: {args.folder}", file=sys.stderr)
        return 2
    text_id = None if args.text_id == "any" else args.text_id

    if args.dry_run:
        print(format_report(dry_run(args.folder, text_id=text_id), wrote=False))
        return 0
    stats = ingest(
        args.folder, get_engine(), text_id=text_id, batch_size=args.batch_size, limit=args.limit
    )
    print(format_report(stats, wrote=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
