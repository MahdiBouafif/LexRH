"""LexRH demo UI. Run it with ``make ui``.

Tab 1 asks the law RAG (cited answer or refusal, as of a chosen date).
Tab 2 runs the case agent on synthetic cases and asks for an explicit human decision.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from lexrh.agent.synthetic import CASES, EMPLOYEES
from lexrh.rag.http import ApiError
from lexrh.ui import services, view

st.set_page_config(page_title="LexRH", page_icon="⚖️", layout="wide")
st.title("LexRH")
st.caption(
    "Aide à la décision en droit du travail français, pas un conseil juridique. "
    "Toutes les données de salariés sont synthétiques."
)

ask_tab, case_tab, audit_tab = st.tabs(["Question juridique", "Dossier de licenciement", "Journal"])

with ask_tab:
    question = st.text_area("Votre question", placeholder="Ex. : quelle est la durée du préavis ?")
    as_of = st.date_input("Droit applicable à la date du", value=date.today())
    if st.button("Poser la question", type="primary", disabled=not question.strip()):
        try:
            with st.spinner("Recherche des articles et rédaction de la réponse..."):
                reply = services.get_assistant().ask(question.strip(), as_of)
        except ApiError as exc:
            st.error(f"Le modèle de réponse est indisponible : {exc}")
        else:
            if reply.refused:
                st.warning(view.refusal_text(reply))
            else:
                st.markdown(reply.answer)
                st.subheader("Sources")
                for source in reply.sources:
                    st.markdown("- " + view.source_line(source))
            with st.expander("Articles montrés au modèle"):
                st.write(", ".join(reply.retrieved) or "aucun")
                if reply.rewritten:
                    st.caption(f"Requête complémentaire : {reply.rewritten}")

with case_tab:
    case_id = st.selectbox("Dossier", sorted(CASES), key="case_id")
    case = CASES[case_id]
    employee = EMPLOYEES[case.employee_id]
    st.caption(
        f"{employee.name} · {case.type} · faute : {case.fault} · notification le "
        f"{case.notification_date}"
    )
    if st.button("Lancer l'agent"):
        st.session_state["result"] = services.get_agent().start(case_id)
        st.session_state["result_case"] = case_id

    result = st.session_state.get("result")
    if result is not None and st.session_state.get("result_case") == case_id:
        st.markdown(f"**Statut : {view.status_label(result.status)}**")
        if result.state.get("reason"):
            st.info(f"Motif : {result.state['reason']}")
        proposal = result.proposal
        if result.status == "awaiting_approval" and proposal is not None:
            left, right = st.columns(2)
            left.metric("Préavis légal (mois)", proposal["notice"].get("months") or "n/a")
            right.metric(
                "Indemnité légale de licenciement", view.euros(proposal["severance"]["amount"])
            )
            basis = proposal["legal_basis"]
            st.markdown("**Base légale**")
            if basis.get("refused") or not basis.get("answer"):
                st.warning("Sources juridiques indisponibles pour ce dossier.")
            else:
                st.markdown(basis["answer"])
                for source in basis.get("sources", []):
                    st.markdown(f"- {source['article']} (en vigueur depuis {source['valid_from']})")
            letter = st.text_area("Projet de courrier (modifiable)", proposal["letter"], height=220)
            reviewer = st.text_input("Votre identifiant", value="rh-demo")
            approve, reject = st.columns(2)
            decision = None
            if approve.button("Valider", type="primary"):
                kind = "approve" if letter == proposal["letter"] else "edit"
                decision = {"decision": kind, "by": reviewer, "letter": letter}
            if reject.button("Rejeter"):
                decision = {"decision": "reject", "by": reviewer, "reason": "rejected in UI"}
            if decision is not None:
                st.session_state["result"] = services.get_agent().resume(case_id, decision)
                st.rerun()
        elif result.status == "executed":
            st.success("Courrier enregistré comme version finale (rien n'a été envoyé).")
            st.code(result.state["final_letter"], language=None)

with audit_tab:
    entries = services.get_audit().entries()
    st.caption(f"{len(entries)} entrées · chaîne intacte : {services.get_audit().verify()}")
    st.dataframe(
        [
            {
                "heure": e.ts,
                "dossier": e.case_id,
                "acteur": e.actor,
                "action": e.action,
                "outil": e.tool or "",
                "résultat": e.result,
            }
            for e in reversed(entries)
        ],
        use_container_width=True,
    )
