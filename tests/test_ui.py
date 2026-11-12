"""UI tests: Streamlit's headless runner with fake services (no models, no database)."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from lexrh.agent.audit import MemoryAuditLog  # noqa: E402
from lexrh.agent.graph import CaseAgent  # noqa: E402
from lexrh.agent.tools import InProcessTools  # noqa: E402
from lexrh.mcp_server.server import build_server  # noqa: E402
from lexrh.rag.answer import Answer, Source  # noqa: E402
from lexrh.rag.http import ApiError  # noqa: E402
from lexrh.ui import services, view  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "src" / "lexrh" / "ui" / "app.py")


class FakeAssistant:
    def ask(self, question: str, as_of: date) -> Answer:
        if "gratin" in question:
            return Answer(question, as_of, "", True, "model_refused")
        if "panne" in question:
            raise ApiError("model down")
        source = Source("L1234-1", "LEGIARTI1", date(2020, 1, 1), None, "VIGUEUR", ["Livre II"])
        return Answer(question, as_of, "Deux mois de préavis.", False, None, [source], ["L1234-1"])


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> MemoryAuditLog:
    audit = MemoryAuditLog()
    assistant = FakeAssistant()
    agent = CaseAgent(InProcessTools(build_server(assistant)), audit)
    monkeypatch.setattr(services, "get_assistant", lambda: assistant)
    monkeypatch.setattr(services, "get_audit", lambda: audit)
    monkeypatch.setattr(services, "get_agent", lambda: agent)
    return audit


def run_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def texts(app: AppTest) -> str:
    parts = [m.value for m in app.markdown] + [w.value for w in app.warning]
    return "\n".join(parts) + "\n" + "\n".join(i.value for i in app.info)


def button(app: AppTest, label: str) -> Any:
    return next(b for b in app.button if b.label == label)


def test_answer_is_shown_with_its_dated_source(fakes: MemoryAuditLog) -> None:
    app = run_app()
    app.text_area[0].set_value("préavis ?").run()
    button(app, "Poser la question").click().run()
    shown = texts(app)
    for expected in ("Deux mois de préavis.", "L1234-1", "toujours en vigueur"):
        assert expected in shown


def test_refusal_is_explained(fakes: MemoryAuditLog) -> None:
    app = run_app()
    app.text_area[0].set_value("recette de gratin").run()
    button(app, "Poser la question").click().run()
    assert any("model_refused" in w.value for w in app.warning)


def test_model_outage_is_an_error_message_not_a_crash(fakes: MemoryAuditLog) -> None:
    app = run_app()
    app.text_area[0].set_value("une panne").run()
    button(app, "Poser la question").click().run()
    assert any("indisponible" in e.value for e in app.error)


def test_ask_button_is_disabled_without_a_question(fakes: MemoryAuditLog) -> None:
    assert button(run_app(), "Poser la question").disabled


def test_agent_stops_for_approval_then_records_the_decision(fakes: MemoryAuditLog) -> None:
    app = run_app()
    button(app, "Lancer l'agent").click().run()
    assert "En attente de décision" in texts(app)
    assert {m.label for m in app.metric} == {
        "Préavis légal (mois)",
        "Indemnité légale de licenciement",
    }
    button(app, "Valider").click().run()
    assert "Validé et enregistré" in texts(app)
    assert any(e.action == "approved" and e.actor == "human:rh-demo" for e in fakes.entries())


def test_rejection_in_the_ui_executes_nothing(fakes: MemoryAuditLog) -> None:
    app = run_app()
    button(app, "Lancer l'agent").click().run()
    button(app, "Rejeter").click().run()
    assert "Rejeté" in texts(app)
    assert not any(e.action == "executed" for e in fakes.entries())


def test_incomplete_case_is_escalated_in_the_ui(fakes: MemoryAuditLog) -> None:
    app = run_app()
    app.selectbox(key="case_id").select("case-004").run()
    button(app, "Lancer l'agent").click().run()
    assert "Escaladé" in texts(app) and "missing_pay_data" in texts(app)


def test_view_helpers() -> None:
    source = Source("L1234-1", "x", date(2020, 1, 1), date(2024, 1, 1), "ABROGE", ["A", "B", "C"])
    line = view.source_line(source)
    assert "jusqu'au 2024-01-01" in line and "B > C" in line and "A >" not in line
    assert view.euros("6933.33") == "6 933,33 €" and view.euros(None) == "n/a"
    assert view.status_label("executed") == "Validé et enregistré"
