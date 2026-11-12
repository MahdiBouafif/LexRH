"""Pure helpers that turn results into display text (tested without Streamlit)."""

from __future__ import annotations

from typing import Any

from lexrh.rag.answer import REFUSAL_REASONS, Answer, Source


def source_line(source: Source) -> str:
    until = f"jusqu'au {source.valid_to}" if source.valid_to else "toujours en vigueur"
    section = f" · {' > '.join(source.hierarchy[-2:])}" if source.hierarchy else ""
    return f"**{source.num}** · en vigueur depuis le {source.valid_from}, {until}{section}"


def refusal_text(answer: Answer) -> str:
    reason = answer.reason or ""
    return f"Pas de réponse ({reason}) : {REFUSAL_REASONS.get(reason, 'raison inconnue')}"


def status_label(status: str) -> str:
    return {
        "awaiting_approval": "En attente de décision",
        "executed": "Validé et enregistré",
        "rejected": "Rejeté",
        "escalated": "Escaladé : intervention humaine nécessaire",
    }.get(status, status)


def euros(value: Any) -> str:
    try:
        return f"{float(value):,.2f} €".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return "n/a"
