import asyncio
from datetime import date
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from lexrh.mcp_server.server import build_server
from lexrh.rag.answer import Answer, Source


class FakeAssistant:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def ask(self, question: str, as_of: date) -> Answer:
        self.calls.append((question, as_of))
        if "gratin" in question:
            return Answer(question, as_of, "", True, "model_refused")
        source = Source("L1234-1", "LEGIARTI1", date(2020, 1, 1), None, "VIGUEUR", ["Livre II"])
        return Answer(question, as_of, "Deux mois.", False, None, [source], ["L1234-1"])


def call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    return asyncio.run(server.call_tool(name, arguments))


def rejected(server: Any, name: str, arguments: dict[str, Any]) -> bool:
    """True when the tool refuses the input with a ToolError (its message reaches the client)."""
    try:
        call(server, name, arguments)
    except ToolError:
        return True
    return False


def structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    data: dict[str, Any] = result.structured_content
    inner: dict[str, Any] = data.get("result", data)
    return inner


def test_the_three_tools_are_listed() -> None:
    tools = asyncio.run(build_server(FakeAssistant()).list_tools())
    assert {tool.name for tool in tools} == {"search_law", "compute_notice", "compute_severance"}
    assert all(tool.description for tool in tools)


def test_search_law_returns_answer_and_dated_sources() -> None:
    fake = FakeAssistant()
    data = structured(
        call(build_server(fake), "search_law", {"question": "préavis ?", "as_of": "2024-05-01"})
    )
    assert data["answer"] == "Deux mois." and not data["refused"]
    assert data["sources"][0]["article"] == "L1234-1"
    assert fake.calls == [("préavis ?", date(2024, 5, 1))]


def test_search_law_passes_a_refusal_through() -> None:
    data = structured(call(build_server(FakeAssistant()), "search_law", {"question": "gratin ?"}))
    assert data["refused"] and data["reason"] == "model_refused" and data["sources"] == []


@pytest.mark.parametrize(
    "arguments",
    [{"question": "ok"}, {"question": "préavis ?", "as_of": "demain"}],
)
def test_search_law_rejects_bad_input(arguments: dict[str, Any]) -> None:
    assert rejected(build_server(FakeAssistant()), "search_law", arguments)


def test_compute_notice() -> None:
    server = build_server(FakeAssistant())
    data = structured(
        call(
            server, "compute_notice", {"hire_date": "2020-01-01", "notification_date": "2026-11-01"}
        )
    )
    assert data["due"] and data["months"] == 2 and data["articles"] == ["L1234-1"]


def test_compute_severance_is_exact() -> None:
    server = build_server(FakeAssistant())
    args = {
        "hire_date": "2021-11-01",
        "notification_date": "2026-11-01",
        "reference_salary": "3000",
    }
    data = structured(call(server, "compute_severance", args))
    assert data["amount"] == "3750.00" and data["due"]


def test_compute_severance_refuses_misconduct() -> None:
    server = build_server(FakeAssistant())
    args = {
        "hire_date": "2010-01-01",
        "notification_date": "2026-11-01",
        "reference_salary": "3000",
        "fault": "gross",
    }
    data = structured(call(server, "compute_severance", args))
    assert not data["due"] and data["amount"] == "0"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (
            "compute_severance",
            {
                "hire_date": "2020-01-01",
                "notification_date": "2026-11-01",
                "reference_salary": "abc",
            },
        ),
        (
            "compute_severance",
            {
                "hire_date": "2020-01-01",
                "notification_date": "2026-11-01",
                "reference_salary": "-5",
            },
        ),
        (
            "compute_severance",
            {
                "hire_date": "2020-01-01",
                "notification_date": "2026-11-01",
                "reference_salary": "NaN",
            },
        ),
        (
            "compute_severance",
            {
                "hire_date": "2020-01-01",
                "notification_date": "2026-11-01",
                "reference_salary": "3000",
                "fault": "bad",
            },
        ),
        ("compute_notice", {"hire_date": "not-a-date", "notification_date": "2026-11-01"}),
    ],
)
def test_calculators_reject_invalid_input(tool: str, arguments: dict[str, Any]) -> None:
    assert rejected(build_server(FakeAssistant()), tool, arguments)
