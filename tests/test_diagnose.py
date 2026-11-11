from datetime import date
from uuid import uuid4

from lexrh.eval.diagnose import Position, format_case, locate, verdict
from lexrh.eval.retrieval import Case
from lexrh.rag.search import SearchHit


def hit(num: str, vector: int | None, keyword: int | None) -> SearchHit:
    return SearchHit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        num=num,
        legal_id="ID",
        valid_from=date(2008, 5, 1),
        valid_to=None,
        status="VIGUEUR",
        hierarchy=[],
        nota="",
        text="t",
        score=1.0,
        vector_rank=vector,
        keyword_rank=keyword,
    )


def test_locate_reports_fused_vector_and_keyword_positions() -> None:
    hits = [hit("L1", 1, None), hit("L2", 5, 3)]
    found, missing = locate(hits, ["L2", "L9"])
    assert (found.fused, found.vector, found.keyword) == (2, 5, 3)
    assert missing.fused is None


def test_verdict_separates_pool_outside_pool_and_not_found() -> None:
    assert "in the rerank pool" in verdict(Position("L1", 3, 3, None))
    assert "outside the pool" in verdict(Position("L1", 35, 35, None))
    assert "NOT FOUND" in verdict(Position("L1", None, None, None))


def test_format_case_mentions_the_article_and_verdict() -> None:
    case = Case("c", "Question ?", date(2023, 1, 1), ("L1",))
    text = format_case(case, [Position("L1", None, None, None)])
    assert "c: Question ?" in text and "L1" in text and "NOT FOUND" in text


def test_default_pause_waits_only_when_a_model_is_called() -> None:
    from lexrh.eval.diagnose import REWRITE_PAUSE, default_pause

    assert default_pause(False, None) == 0.0
    assert default_pause(True, None) == REWRITE_PAUSE
    assert default_pause(True, 5.0) == 5.0
    assert default_pause(True, -3.0) == 0.0
