from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from lexrh.agent.audit import MemoryAuditLog
from lexrh.agent.graph import LAW_QUESTION, AgentResult, CaseAgent, State
from lexrh.agent.synthetic import CASES, EMPLOYEES
from lexrh.agent.tools import InProcessTools, Tools
from lexrh.mcp_server.server import build_server
from lexrh.rag.answer import Answer, Source


class FakeAssistant:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def ask(self, question: str, as_of: date) -> Answer:
        self.questions.append(question)
        source = Source("L1232-2", "LEGIARTI2", date(2020, 1, 1), None, "VIGUEUR", ["Livre II"])
        return Answer(question, as_of, "Convocation puis entretien.", False, None, [source])


class RecordingTools:
    """Wraps real in-process tools and remembers every call."""

    def __init__(self, inner: Tools) -> None:
        self.inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return self.inner.call(name, arguments)


class BrokenSearch:
    def __init__(self, inner: Tools) -> None:
        self.inner = inner

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_law":
            raise RuntimeError("model down")
        return self.inner.call(name, arguments)


@pytest.fixture
def assistant() -> FakeAssistant:
    return FakeAssistant()


@pytest.fixture
def tools(assistant: FakeAssistant) -> RecordingTools:
    return RecordingTools(InProcessTools(build_server(assistant)))


def make(tools: Tools, **kwargs: Any) -> tuple[CaseAgent, MemoryAuditLog]:
    audit = MemoryAuditLog()
    return CaseAgent(tools, audit, **kwargs), audit


def approve() -> dict[str, Any]:
    return {"decision": "approve", "by": "hr-1"}


def test_case_runs_until_the_human_decision(tools: RecordingTools) -> None:
    agent, audit = make(tools)
    result = agent.start("case-001")
    assert result.status == "awaiting_approval" and result.proposal is not None
    assert [name for name, _ in tools.calls] == [
        "compute_notice",
        "compute_severance",
        "search_law",
    ]
    assert result.proposal["notice"]["months"] == 2
    assert result.proposal["severance"]["due"]
    assert "outcome" not in result.state  # nothing is final yet
    assert not any(e.action == "executed" for e in audit.entries())


def test_figures_come_from_the_calculators_not_the_model(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    result = agent.start("case-001")
    assert result.proposal is not None
    # emp-001: hired 2018-03-01, notified 2026-11-12 -> 104 months, salary 3200
    assert result.proposal["severance"]["seniority_months"] == 104
    assert result.proposal["severance"]["amount"] == "6933.33"  # 3200 * (104/12) / 4


def test_approval_executes_and_logs_who_approved(tools: RecordingTools) -> None:
    agent, audit = make(tools)
    agent.start("case-001")
    result = agent.resume("case-001", approve())
    assert result.status == "executed" and result.state["final_letter"]
    actions = [(e.actor, e.action) for e in audit.entries("case-001")]
    assert ("human:hr-1", "approved") in actions and ("agent", "executed") in actions
    assert audit.verify()


def test_human_edit_replaces_the_letter(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    agent.start("case-001")
    result = agent.resume("case-001", {"decision": "edit", "by": "hr-2", "letter": "Texte corrigé"})
    assert result.status == "executed" and result.state["final_letter"] == "Texte corrigé"


def test_rejection_executes_nothing(tools: RecordingTools) -> None:
    agent, audit = make(tools)
    agent.start("case-001")
    result = agent.resume("case-001", {"decision": "reject", "by": "hr-1", "reason": "non"})
    assert result.status == "rejected" and "final_letter" not in result.state
    assert not any(e.action == "executed" for e in audit.entries())


def test_unknown_decision_is_treated_as_a_rejection(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    agent.start("case-001")
    assert agent.resume("case-001", {"decision": "yolo"}).status == "rejected"


def test_empty_edit_is_not_approved(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    agent.start("case-001")
    assert agent.resume("case-001", {"decision": "edit", "letter": "  "}).status == "rejected"


def test_missing_pay_data_escalates_before_any_tool_call(tools: RecordingTools) -> None:
    agent, audit = make(tools)
    result = agent.start("case-004")
    assert result.status == "escalated" and result.state["reason"] == "missing_pay_data"
    assert tools.calls == []
    assert audit.entries("case-004")[-1].action == "escalated"


def test_unsupported_case_type_escalates(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    result = agent.start("case-005")
    assert result.status == "escalated" and "unsupported_case_type" in result.state["reason"]
    assert tools.calls == []


def test_unknown_case_escalates(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    assert agent.start("case-999").state["reason"] == "unknown_case"


def test_serious_misconduct_gives_no_notice_or_severance(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    result = agent.start("case-003")
    assert result.proposal is not None
    assert not result.proposal["notice"]["due"] and not result.proposal["severance"]["due"]
    assert "faute grave" in result.proposal["letter"]


def test_short_seniority_gets_no_severance(tools: RecordingTools) -> None:
    agent, _ = make(tools)
    result = agent.start("case-002")  # 6 months
    assert result.proposal is not None and not result.proposal["severance"]["due"]


def test_law_question_contains_no_personal_data(
    tools: RecordingTools, assistant: FakeAssistant
) -> None:
    agent, _ = make(tools)
    agent.start("case-001")
    assert assistant.questions == [LAW_QUESTION]
    for employee in EMPLOYEES.values():
        assert employee.name not in assistant.questions[0]
        assert employee.name not in str(tools.calls)


def test_step_limit_stops_the_agent(tools: RecordingTools) -> None:
    agent, audit = make(tools, max_steps=3)
    result = agent.start("case-001")
    assert result.status == "escalated" and result.state["reason"] == "step_limit"
    assert len(tools.calls) < 3


def test_case_continues_when_the_law_search_fails(tools: RecordingTools) -> None:
    agent, audit = make(BrokenSearch(tools))
    result = agent.start("case-001")
    assert result.status == "awaiting_approval" and result.proposal is not None
    assert result.proposal["legal_basis"]["reason"] == "tool_unavailable"
    assert any(e.action == "tool_failed" for e in audit.entries())


def test_every_tool_call_is_audited_without_its_arguments(tools: RecordingTools) -> None:
    agent, audit = make(tools)
    agent.start("case-001")
    calls = [e for e in audit.entries() if e.action == "tool_call"]
    assert [e.tool for e in calls] == ["compute_notice", "compute_severance", "search_law"]
    assert all(e.args_hash for e in calls)
    assert "2018-03-01" not in repr(audit.entries())


def test_execution_is_idempotent(tools: RecordingTools) -> None:
    agent, audit = make(tools)
    agent.start("case-001")
    first: AgentResult = agent.resume("case-001", approve())
    again = State(case_id="case-001", letter=first.state["letter"])
    agent.execute(again)  # same case, same letter, run again
    assert sum(e.action == "executed" for e in audit.entries()) == 1
    assert any(e.action == "execute_skipped" for e in audit.entries())


def test_graph_is_built_from_the_documented_nodes(tools: RecordingTools) -> None:
    drawing = make(tools)[0].graph.get_graph().draw_mermaid()
    for node in ("load_case", "gather", "assess", "compute", "legal_basis", "draft", "approval"):
        assert node in drawing


def test_custom_data_can_be_injected(tools: RecordingTools) -> None:
    employee = replace(EMPLOYEES["emp-001"], id="emp-x")
    case = replace(CASES["case-001"], id="case-x", employee_id="emp-x")
    agent, _ = make(tools, employees={"emp-x": employee}, cases={"case-x": case})
    assert agent.start("case-x").status == "awaiting_approval"
