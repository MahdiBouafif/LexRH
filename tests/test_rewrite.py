import json
from datetime import date

from lexrh.rag.http import ApiError
from lexrh.rag.rewrite import MAX_QUERY_CHARS, LLMQueryRewriter, parse_query

D = date(2023, 1, 1)


class FakeGenerator:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.seen: list[str] = []

    def generate(self, messages, schema):  # type: ignore[no-untyped-def]
        self.seen.append(messages[-1]["content"])
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_parse_returns_the_rewritten_query() -> None:
    raw = json.dumps({"query": "  poursuites   disciplinaires \n délai "})
    assert parse_query(raw, "sanctionner une faute") == "poursuites disciplinaires délai"


def test_parse_accepts_a_code_fence() -> None:
    raw = '```json\n{"query": "prescription des faits fautifs"}\n```'
    assert parse_query(raw, "q") == "prescription des faits fautifs"


def test_parse_rejects_unusable_output() -> None:
    for raw in ("pas du json", "[]", json.dumps({"query": 3}), json.dumps({"query": "  "})):
        assert parse_query(raw, "q") is None


def test_parse_drops_a_rewrite_identical_to_the_question() -> None:
    assert parse_query(json.dumps({"query": "Même Question"}), "même question") is None


def test_parse_caps_the_length() -> None:
    long = json.dumps({"query": "mot " * 500})
    result = parse_query(long, "q")
    assert result is not None and len(result) <= MAX_QUERY_CHARS


def test_rewriter_falls_back_to_none_when_the_model_is_down() -> None:
    rewriter = LLMQueryRewriter(FakeGenerator(ApiError("429")))
    assert rewriter.rewrite("question", D) is None


def test_rewriter_sends_the_question_as_data() -> None:
    generator = FakeGenerator(json.dumps({"query": "x y"}))
    assert LLMQueryRewriter(generator).rewrite("ma question", D) == "x y"
    assert "ma question" in generator.seen[0]


def test_the_word_null_is_not_a_search_query() -> None:
    assert parse_query('{"query": "null"}', "question") is None
