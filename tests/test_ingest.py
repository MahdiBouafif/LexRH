import shutil
from pathlib import Path

from lexrh.rag.ingest import CODE_DU_TRAVAIL, Stats, article_to_row, dry_run, format_report, scan
from lexrh.rag.parser import parse_article_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_article_to_row_maps_fields() -> None:
    row = article_to_row(parse_article_file(FIXTURES / "LEGIARTI000006901112.xml"))
    assert row["legal_id"] == "LEGIARTI000006901112"
    assert row["title"] == "Code du travail - art. L1234-1"
    assert row["source"] == "LEGI"
    assert row["status"] == "VIGUEUR"
    assert row["valid_to"] is None
    assert row["text_id"] == CODE_DU_TRAVAIL
    assert len(row["hierarchy"]) == 7
    assert row["text"].startswith("Lorsque le licenciement")
    assert len(row["content_hash"]) == 64


def test_scan_keeps_only_the_code_du_travail_by_default(known_articles: Path) -> None:
    stats = Stats()
    rows = list(scan(known_articles, stats))
    assert stats.seen == 12
    assert len(rows) == stats.ready == 8
    assert stats.skipped_other_text == 4  # the four DGSE decree articles
    assert stats.failed == 0
    assert {row["text_id"] for row in rows} == {CODE_DU_TRAVAIL}


def test_scan_without_text_filter_still_drops_non_indexable_versions(
    known_articles: Path,
) -> None:
    stats = Stats()
    rows = list(scan(known_articles, stats, text_id=None))
    assert stats.seen == 12
    assert len(rows) == 11
    assert stats.skipped_not_indexable == 1  # article 41 of the decree: placeholder start date
    assert stats.skipped_other_text == 0


def test_broken_files_are_counted_not_fatal(tmp_path: Path) -> None:
    shutil.copy(FIXTURES / "LEGIARTI000006901112.xml", tmp_path / "LEGIARTI000006901112.xml")
    (tmp_path / "LEGIARTI000000000001.xml").write_text("<ARTICLE><META>", encoding="utf-8")
    (tmp_path / "LEGIARTI000000000002.xml").write_text("<AUTRE/>", encoding="utf-8")
    stats = dry_run(tmp_path)
    assert stats.seen == 3
    assert stats.failed == 2
    assert stats.ready == 1
    assert len(stats.failures) == 2


def test_dry_run_reports_etat_counts(known_articles: Path) -> None:
    stats = dry_run(known_articles)
    assert stats.etat_counts["VIGUEUR"] == 3
    assert stats.etat_counts["TRANSFERE"] == 3  # D122-11 (1989-91), D122-12 (two versions)
    report = format_report(stats, wrote=False)
    assert "files seen:" in report
    assert "VIGUEUR" in report
