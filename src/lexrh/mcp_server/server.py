"""MCP server: the law RAG and the deterministic HR calculators as typed tools.

Run it with ``make mcp`` (stdio transport) and point any MCP client at it. Three tools:

    search_law(question, as_of)                     read   cited answer from the Code du travail
    compute_notice(hire_date, notification, fault)  compute  legal notice period
    compute_severance(hire_date, notification,      compute  legal severance pay
                      reference_salary, fault)

The tools return plain dicts. Arguments are validated by their types (dates are ISO strings,
the salary a positive decimal, the fault one of three values); invalid input is rejected before
any calculation. The calculators are code, not an LLM: the money figure is reproducible.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from lexrh.hr.rules import Misconduct, legal_notice, legal_severance
from lexrh.rag.answer import Answer

Fault = Literal["none", "serious", "gross"]

INSTRUCTIONS = (
    "French labour-law tools. search_law answers from the Code du travail with verified "
    "citations, as the law stood on a date, and refuses when the articles do not allow an "
    "answer. The compute_* tools give LEGAL MINIMUMS only (a collective agreement may be more "
    "favourable). Decision support, not legal advice."
)


class Asker(Protocol):
    def ask(self, question: str, as_of: date) -> Answer: ...


def _date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def answer_to_dict(result: Answer) -> dict[str, Any]:
    return {
        "answer": result.answer,
        "refused": result.refused,
        "reason": result.reason,
        "as_of": result.as_of.isoformat(),
        "sources": [
            {
                "article": source.num,
                "valid_from": source.valid_from.isoformat(),
                "valid_to": source.valid_to.isoformat() if source.valid_to else None,
                "section": source.hierarchy,
            }
            for source in result.sources
        ],
    }


def build_server(assistant: Asker | None = None) -> MCPServer:
    """Create the server. ``assistant`` is built lazily from the settings when not given
    (so importing this module never loads the reranker or opens a connection)."""
    server = MCPServer("lexrh", instructions=INSTRUCTIONS)
    holder: dict[str, Asker] = {}
    if assistant is not None:
        holder["assistant"] = assistant

    def get_assistant() -> Asker:
        if "assistant" not in holder:
            from lexrh.rag.answer import build_assistant

            holder["assistant"] = build_assistant()
        return holder["assistant"]

    @server.tool()
    def search_law(question: str, as_of: str | None = None) -> dict[str, Any]:
        """Answer a French labour-law question from the Code du travail, as in force on
        ``as_of`` (ISO date, default today). Returns the answer, its cited articles with their
        validity dates, or a refusal with a reason."""
        if not 3 <= len(question) <= 1000:
            raise ToolError("question must be between 3 and 1000 characters")
        day = _date(as_of, "as_of") if as_of else date.today()
        return answer_to_dict(get_assistant().ask(question, day))

    @server.tool()
    def compute_notice(
        hire_date: str, notification_date: str, fault: Fault = "none"
    ) -> dict[str, Any]:
        """Legal notice period (L1234-1) for a dismissal for a personal reason.
        ``fault`` is none, serious (faute grave) or gross (faute lourde)."""
        notice = legal_notice(
            _date(hire_date, "hire_date"),
            _date(notification_date, "notification_date"),
            Misconduct(fault),
        )
        return {
            "due": notice.due,
            "months": notice.months,
            "articles": list(notice.articles),
            "notes": list(notice.notes),
        }

    @server.tool()
    def compute_severance(
        hire_date: str,
        notification_date: str,
        reference_salary: str,
        fault: Fault = "none",
    ) -> dict[str, Any]:
        """Legal severance pay (L1234-9, R1234-2) for a dismissal for a personal reason.
        ``reference_salary`` is the gross monthly reference salary (R1234-4), as a decimal
        string such as "3000.00"."""
        try:
            salary = Decimal(reference_salary)
        except InvalidOperation as exc:
            raise ToolError(f"reference_salary must be a number, got {reference_salary!r}") from exc
        if not salary.is_finite() or salary <= 0:
            raise ToolError("reference_salary must be a positive number")
        result = legal_severance(
            _date(hire_date, "hire_date"),
            _date(notification_date, "notification_date"),
            salary,
            Misconduct(fault),
        )
        return {
            "due": result.due,
            "amount": str(result.amount),
            "seniority_months": result.seniority_months,
            "reference_salary": str(result.reference_salary),
            "articles": list(result.articles),
            "notes": list(result.notes),
        }

    return server


def main() -> int:
    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
