import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from lexrh.api.main import create_app, get_assistant
from lexrh.rag.answer import Answer, Source, answer_schema, build_messages, interpret
from lexrh.rag.generate import GenerationError, OpenAICompatibleGenerator

NUMS = ["L1234-1", "L1234-9"]


def raw(**kw: object) -> str:
    return json.dumps({"answer": "Texte.", "citations": ["L1234-1"], "refusal": False} | kw)


def test_interpret_keeps_valid_citations_and_drops_invented_ones() -> None:
    r = interpret(raw(citations=["L1234-1", "L9999-9", "L1234-1"]), NUMS)
    assert r is not None
    assert r.citations == ["L1234-1"]
    assert r.invalid == ["L9999-9"]


@pytest.mark.parametrize(
    "bad",
    ["not json", "[]", json.dumps({"answer": "x"}), raw(refusal="no"), raw(citations="L1234-1")],
)
def test_interpret_rejects_malformed_output(bad: str) -> None:
    assert interpret(bad, NUMS) is None


def test_schema_restricts_citations_to_provided_numbers() -> None:
    schema = answer_schema(NUMS)
    assert schema["properties"]["citations"]["items"]["enum"] == NUMS


def test_question_is_before_and_after_the_articles() -> None:
    user = build_messages("Quel préavis ?", date(2020, 1, 1), "[L1234-1] ...")[1]["content"]
    assert user.startswith("Question : Quel préavis ?")
    assert user.rstrip().endswith("Quel préavis ?")


def test_generator_sends_schema_and_returns_content() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": raw()}}]})

    gen = OpenAICompatibleGenerator(
        "http://x/v1/",
        "m",
        api_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    out = gen.generate([{"role": "user", "content": "q"}], answer_schema(NUMS))
    assert json.loads(out)["refusal"] is False
    assert seen["temperature"] == 0
    assert seen["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert seen["auth"] == "Bearer k"


def test_generator_rejects_unexpected_response_shape() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(GenerationError):
        OpenAICompatibleGenerator("http://x/v1", "m", client=client).generate([], {})


class FakeAssistant:
    def __init__(self, result: Answer | Exception) -> None:
        self.result = result

    def ask(self, question: str, as_of: date) -> Answer:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client_for(result: Answer | Exception) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_assistant] = lambda: FakeAssistant(result)
    return TestClient(app)


def test_ask_returns_answer_and_sources() -> None:
    src = Source("L1234-1", "LEGIARTI1", date(2008, 5, 1), None, "VIGUEUR", ["Livre II"])
    result = Answer("q", date(2020, 1, 1), "Texte.", False, None, [src], ["L1234-1"])
    resp = client_for(result).post(
        "/ask", json={"question": "Quel préavis ?", "date": "2020-01-01"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["refused"] is False
    assert body["sources"][0]["num"] == "L1234-1"


def test_ask_reports_refusal_with_reason() -> None:
    result = Answer("q", date(2020, 1, 1), "", True, "model_refused")
    body = client_for(result).post("/ask", json={"question": "Hors sujet ?"}).json()
    assert body["refused"] is True and body["reason"] == "model_refused"


def test_ask_returns_503_when_a_model_is_down() -> None:
    resp = client_for(GenerationError("down")).post("/ask", json={"question": "Quel préavis ?"})
    assert resp.status_code == 503


def test_ask_validates_input() -> None:
    assert client_for(GenerationError("x")).post("/ask", json={"question": "a"}).status_code == 422


def test_generator_falls_back_to_prompted_json_when_schema_is_refused() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(
                501, json={"error": {"message": "structured output is unavailable"}}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": raw()}}]})

    gen = OpenAICompatibleGenerator(
        "http://x/v1",
        "m",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    messages = [{"role": "user", "content": "q"}]
    assert json.loads(gen.generate(messages, answer_schema(NUMS)))["refusal"] is False
    assert "response_format" not in bodies[-1]
    assert "JSON" in bodies[-1]["messages"][-1]["content"]  # type: ignore[index]
    gen.generate(messages, answer_schema(NUMS))
    assert "response_format" not in bodies[-1]  # remembered: no second schema attempt


def test_interpret_accepts_json_inside_a_code_fence() -> None:
    assert interpret("```json\n" + raw() + "\n```", NUMS) is not None


def test_article_shows_its_section_so_special_regimes_are_visible() -> None:
    from lexrh.rag.answer import format_article

    out = format_article(
        "L7313-9", date(2008, 5, 1), None, "Texte.", "", ["Partie", "Voyageurs, représentants"]
    )
    assert "Section : Partie > Voyageurs, représentants" in out
