"""Case agent: a LangGraph state machine that prepares a dismissal case and stops for a human.

    load_case -> gather -> assess -> compute -> legal_basis -> draft -> approval -> execute
        |           |         |
        +-----------+---------+--> END with outcome "escalated" (unsupported case, missing data,
                                   step limit)               approval --> END "rejected"

What is deterministic and what is not
    * Routing, data checks and every figure are code. The notice and the severance come from the
      MCP calculator tools (``compute_notice``, ``compute_severance``), not from a model.
    * The only language-model step is ``search_law`` (cited legal basis), reached through the MCP
      server. Its question is a fixed sentence with no personal data. If the tool fails, the case
      continues with "sources unavailable" instead of stopping.
    * Nothing is finalised without an explicit human decision (``interrupt``). "Execute" records
      the approved letter as final; it sends nothing anywhere.

Safety properties (each tested): step limit, audit entry for every step and every tool call,
idempotent execution (same case and same letter: done once), no personal data in the law query.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from lexrh.agent.audit import AuditLog, hash_args
from lexrh.agent.synthetic import CASES, EMPLOYEES, Case, Employee
from lexrh.agent.tools import Tools
from lexrh.hr.rules import reference_salary

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 12
LAW_QUESTION = (
    "Quelle est la procédure à respecter pour un licenciement pour motif personnel "
    "d'un salarié en CDI ?"
)
AGENT = "agent"


class State(TypedDict, total=False):
    case_id: str
    steps: int
    employee_id: str
    reference_salary: str
    notice: dict[str, Any]
    severance: dict[str, Any]
    legal_basis: dict[str, Any]
    letter: str
    decision: dict[str, Any]
    outcome: str  # escalated | rejected | executed
    reason: str
    final_letter: str


@dataclass(frozen=True)
class AgentResult:
    status: str  # awaiting_approval | executed | rejected | escalated
    state: dict[str, Any]
    proposal: dict[str, Any] | None = None  # what the human is asked to decide on


def draft_letter(
    case: Case, employee: Employee, notice: dict[str, Any], severance: dict[str, Any]
) -> str:
    """Deterministic draft summarising the case. A human edits and approves it."""
    if case.fault != "none":
        due = "Aucun préavis ni indemnité légale de licenciement n'est dû (faute grave ou lourde)."
    else:
        months = notice.get("months")
        notice_text = (
            f"{months} mois de préavis" if months else "préavis fixé par la convention ou l'usage"
        )
        due = (
            f"Préavis légal minimal : {notice_text}. "
            f"Indemnité légale de licenciement minimale : {severance['amount']} € brut "
            f"(ancienneté : {severance['seniority_months']} mois)."
        )
    return (
        f"PROJET - dossier {case.id} - licenciement pour motif personnel\n"
        f"Salarié : {employee.name} (CDI depuis le {employee.hire_date})\n"
        f"Date de notification : {case.notification_date}\n"
        f"{due}\n"
        "Minima légaux uniquement : une convention collective ou le contrat peuvent être plus "
        "favorables. Document d'aide à la décision, à valider par un responsable RH."
    )


class CaseAgent:
    def __init__(
        self,
        tools: Tools,
        audit: AuditLog,
        *,
        employees: dict[str, Employee] | None = None,
        cases: dict[str, Case] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.tools = tools
        self.audit = audit
        self.employees = EMPLOYEES if employees is None else employees
        self.cases = CASES if cases is None else cases
        self.max_steps = max_steps
        self.graph = self._build()

    # -- helpers -------------------------------------------------------------------------

    def _log(self, case_id: str, action: str, result: str = "", **kwargs: Any) -> None:
        self.audit.record(case_id, kwargs.pop("actor", AGENT), action, result=result, **kwargs)

    def _call(self, case_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = self.tools.call(name, args)
        self.audit.record(case_id, AGENT, "tool_call", tool=name, args=args, result=str(result))
        return result

    def _escalate(self, state: State, reason: str) -> dict[str, Any]:
        self._log(state["case_id"], "escalated", reason)
        return {"outcome": "escalated", "reason": reason}

    def _step(self, name: str, fn: Any) -> Any:
        """Wrap a node: enforce the step limit and count the step."""

        def run(state: State) -> dict[str, Any]:
            steps = state.get("steps", 0)
            if steps >= self.max_steps:
                return self._escalate(state, "step_limit")
            update: dict[str, Any] = fn(state)
            update.setdefault("steps", steps + 1)
            return update

        run.__name__ = name
        return run

    # -- nodes ---------------------------------------------------------------------------

    def load_case(self, state: State) -> dict[str, Any]:
        case = self.cases.get(state["case_id"])
        if case is None:
            return self._escalate(state, "unknown_case")
        if case.type != "dismissal_personal":
            return self._escalate(state, f"unsupported_case_type:{case.type}")
        self._log(case.id, "case_loaded", case.type)
        return {"employee_id": case.employee_id}

    def gather(self, state: State) -> dict[str, Any]:
        employee = self.employees.get(state["employee_id"])
        if employee is None:
            return self._escalate(state, "unknown_employee")
        self._log(
            state["case_id"], "employee_read", f"{len(employee.last_12_months_pay)} pay months"
        )
        return {}

    def assess(self, state: State) -> dict[str, Any]:
        employee = self.employees[state["employee_id"]]
        if not employee.last_12_months_pay:
            return self._escalate(state, "missing_pay_data")
        pay = list(employee.last_12_months_pay)
        salary = reference_salary(pay, pay[-3:])
        self._log(state["case_id"], "assessed", f"reference salary {salary}")
        return {"reference_salary": str(salary)}

    def compute(self, state: State) -> dict[str, Any]:
        case, employee = self.cases[state["case_id"]], self.employees[state["employee_id"]]
        common = {
            "hire_date": employee.hire_date.isoformat(),
            "notification_date": case.notification_date.isoformat(),
            "fault": case.fault,
        }
        notice = self._call(case.id, "compute_notice", common)
        severance = self._call(
            case.id, "compute_severance", {**common, "reference_salary": state["reference_salary"]}
        )
        return {"notice": notice, "severance": severance}

    def legal_basis(self, state: State) -> dict[str, Any]:
        case = self.cases[state["case_id"]]
        args = {"question": LAW_QUESTION, "as_of": case.notification_date.isoformat()}
        try:
            basis = self._call(case.id, "search_law", args)
        except Exception as exc:  # the case continues without the legal text
            logger.warning("search_law unavailable: %s", exc)
            self._log(case.id, "tool_failed", type(exc).__name__, tool="search_law", args=args)
            basis = {"refused": True, "reason": "tool_unavailable", "answer": "", "sources": []}
        return {"legal_basis": basis}

    def draft(self, state: State) -> dict[str, Any]:
        case, employee = self.cases[state["case_id"]], self.employees[state["employee_id"]]
        letter = draft_letter(case, employee, state["notice"], state["severance"])
        self._log(case.id, "approval_requested", "draft ready")
        return {"letter": letter}

    def approval(self, state: State) -> dict[str, Any]:
        # Nothing with side effects may run before interrupt(): this node runs again on resume.
        decision = interrupt(
            {
                "case_id": state["case_id"],
                "letter": state["letter"],
                "notice": state["notice"],
                "severance": state["severance"],
                "legal_basis": state["legal_basis"],
            }
        )
        kind, reviewer = decision.get("decision"), str(decision.get("by", "unknown"))
        actor = f"human:{reviewer}"
        if kind == "approve":
            self._log(state["case_id"], "approved", actor=actor)
            return {"decision": decision}
        if kind == "edit":
            edited = str(decision.get("letter", "")).strip()
            if not edited:
                self._log(state["case_id"], "rejected", "empty edit", actor=actor)
                return {"decision": decision, "outcome": "rejected", "reason": "empty_edit"}
            self._log(state["case_id"], "edited_and_approved", actor=actor)
            return {"decision": decision, "letter": edited}
        self._log(state["case_id"], "rejected", str(decision.get("reason", "")), actor=actor)
        return {"decision": decision, "outcome": "rejected", "reason": "rejected_by_human"}

    def execute(self, state: State) -> dict[str, Any]:
        letter = state["letter"]
        key = hashlib.sha256(f"{state['case_id']}|{letter}".encode()).hexdigest()
        done = {e.args_hash for e in self.audit.entries(state["case_id"]) if e.action == "executed"}
        if hash_args({"idempotency": key}) in done:
            self._log(state["case_id"], "execute_skipped", "already executed")
        else:
            self.audit.record(
                state["case_id"],
                AGENT,
                "executed",
                args={"idempotency": key},
                result="letter recorded as final",
            )
        return {"outcome": "executed", "final_letter": letter}

    # -- graph ---------------------------------------------------------------------------

    @staticmethod
    def _next(node: str) -> Any:
        def route(state: State) -> str:
            return END if "outcome" in state else node

        return route

    def _build(self) -> Any:
        graph = StateGraph(State)
        order = [
            ("load_case", self.load_case),
            ("gather", self.gather),
            ("assess", self.assess),
            ("compute", self.compute),
            ("legal_basis", self.legal_basis),
            ("draft", self.draft),
            ("approval", self.approval),
            ("execute", self.execute),
        ]
        for name, fn in order:
            graph.add_node(name, self._step(name, fn))
        graph.add_edge(START, "load_case")
        for (name, _), (following, _) in zip(order, order[1:], strict=False):
            graph.add_conditional_edges(name, self._next(following), [following, END])
        graph.add_edge("execute", END)
        return graph.compile(checkpointer=InMemorySaver())

    # -- public API ----------------------------------------------------------------------

    def _result(self, state: dict[str, Any]) -> AgentResult:
        pending = state.get("__interrupt__")
        if pending:
            return AgentResult("awaiting_approval", state, pending[0].value)
        return AgentResult(str(state.get("outcome", "escalated")), state)

    def start(self, case_id: str) -> AgentResult:
        config = {"configurable": {"thread_id": case_id}}
        return self._result(self.graph.invoke({"case_id": case_id, "steps": 0}, config))

    def resume(self, case_id: str, decision: dict[str, Any]) -> AgentResult:
        config = {"configurable": {"thread_id": case_id}}
        return self._result(self.graph.invoke(Command(resume=decision), config))


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from lexrh.agent.audit import FileAuditLog
    from lexrh.agent.tools import InProcessTools
    from lexrh.mcp_server.server import build_server

    parser = argparse.ArgumentParser(description="Run the dismissal case agent.")
    parser.add_argument("case_id", choices=sorted(CASES))
    parser.add_argument("--stdio", action="store_true", help="use the MCP server over stdio")
    parser.add_argument("--audit", default="data/audit.jsonl")
    args = parser.parse_args(argv)

    if args.stdio:
        from lexrh.agent.tools import StdioMcpTools

        tools: Tools = StdioMcpTools()
    else:
        tools = InProcessTools(build_server())
    audit = FileAuditLog(Path(args.audit))
    agent = CaseAgent(tools, audit)
    result = agent.start(args.case_id)
    if result.status == "awaiting_approval" and result.proposal:
        print("\n" + result.proposal["letter"])
        basis = result.proposal["legal_basis"]
        print("\nLegal basis:", basis.get("answer") or f"unavailable ({basis.get('reason')})")
        choice = input("\nDecision [a]pprove / [r]eject: ").strip().lower()
        decision = {"decision": "approve" if choice == "a" else "reject", "by": "cli-user"}
        result = agent.resume(args.case_id, decision)
    print(f"\nOutcome: {result.status}  {result.state.get('reason', '')}".rstrip())
    print(f"Audit chain intact: {audit.verify()}  ({len(audit.entries(args.case_id))} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
