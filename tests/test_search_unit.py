import uuid

from lexrh.rag.search import keyword_query, reciprocal_rank_fusion


def test_keyword_query_is_an_or_of_distinct_lowercase_words() -> None:
    assert keyword_query("Quel est le préavis de licenciement ? Le préavis") == (
        "quel | est | préavis | licenciement"
    )


def test_keyword_query_drops_short_words_and_symbols() -> None:
    assert keyword_query("L1234-1 ou  à  (un) 'test'; DROP") == "l1234 | test | drop"


def test_keyword_query_of_nothing_is_none() -> None:
    assert keyword_query("?! à , ") is None


def test_rrf_rewards_items_found_by_both_rankings() -> None:
    a, b, c = (uuid.UUID(int=i) for i in (1, 2, 3))
    fused = reciprocal_rank_fusion([[a, b], [c, b]])
    ranking = [item for item, _ in fused]
    assert ranking[0] == b  # second in both lists beats first in only one
    assert set(ranking) == {a, b, c}


def test_rrf_score_formula() -> None:
    a = uuid.UUID(int=1)
    ((_, score),) = reciprocal_rank_fusion([[a], [a]], k=60)
    assert abs(score - 2 / 61) < 1e-12


def test_rrf_is_deterministic_on_ties() -> None:
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    assert reciprocal_rank_fusion([[a], [b]]) == reciprocal_rank_fusion([[a], [b]])
    assert reciprocal_rank_fusion([]) == []
