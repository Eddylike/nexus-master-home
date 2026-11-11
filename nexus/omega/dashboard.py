"""OMEGA Dashboard (Streamlit, optional).

Start: python -m nexus.omega.cli dashboard
"""
from __future__ import annotations

import json

from nexus.omega import config, events, registry, sandbox
from nexus.omega.agent import OmegaAgent


def _guard_decide(approval_id: str, approve: bool, by: str = "OMEGA-Dashboard") -> dict:
    from nexus.terminal import guard

    res = guard.decide(approval_id, approve, by=by)
    if res.get("success"):
        events.set_approval_status(approval_id, "approved" if approve else "denied", by)
    return res


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="OMEGA", page_icon="Ω", layout="wide")
    st.title("OMEGA - autonomer NEXUS-Agent")
    st.caption(f"Home: {config.home()} | HITL: {config.hitl_mode()}")

    with st.sidebar:
        st.subheader("Sandbox")
        st.json(sandbox.status())
        st.subheader("Tools")
        tools = registry.list_tools()
        st.metric("registriert", len(tools))
        for tool in tools:
            mark = "approval" if (tool["destructive"] or tool["dynamic_guard"]) else "free"
            st.caption(f"{tool['name']} - {tool['permission']} / {mark}")

    tab_run, tab_runs, tab_approvals, tab_rag = st.tabs(
        ["Auftrag", "Runs", "Freigaben", "RAG"])

    with tab_run:
        goal = st.text_area("Ziel", value="Recherchiere die wichtigsten AI-News und poste "
                                           "eine Zusammenfassung in Slack.", height=100)
        context = st.text_input("Kontext (optional)")
        col1, col2 = st.columns([1, 4])
        with col1:
            start = st.button("Start", type="primary")
        with col2:
            st.caption("Schreibende Werkzeuge pausieren und warten auf Freigabe.")
        if start and goal.strip():
            with st.spinner("OMEGA arbeitet ..."):
                st.session_state["last"] = OmegaAgent().run(goal, context=context)
        result = st.session_state.get("last")
        if result:
            status = result.get("status")
            st.markdown(f"**Status:** `{status}`  |  **Run:** `{result.get('run_id')}`")
            if result.get("approval_id"):
                st.warning("Freigabe erforderlich: " + str(result["approval_id"]))
                c1, c2 = st.columns(2)
                if c1.button("Freigeben und fortsetzen", type="primary"):
                    _guard_decide(result["approval_id"], True)
                    st.session_state["last"] = OmegaAgent().resume(
                        result["run_id"], approval_id=result["approval_id"])
                    st.rerun()
                if c2.button("Verweigern"):
                    _guard_decide(result["approval_id"], False)
                    st.info("Freigabe verweigert. Der Run bleibt unveraendert.")
            st.subheader("Ergebnis")
            st.text(result.get("final") or result.get("error") or "")
            sources = result.get("sources") or []
            if sources:
                st.subheader("Quellen")
                for src in sources:
                    st.markdown(f"- [{src.get('title')}]({src.get('url')})")
            with st.expander("Plan / Trace"):
                st.json({"plan": result.get("plan"), "trace": result.get("trace")})
            with st.expander("Tool-Calls"):
                st.dataframe(result.get("tool_calls") or [], use_container_width=True)
            with st.expander("Rohresultat"):
                st.code(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                        language="json")

    with tab_runs:
        runs = events.list_runs(30)
        if runs:
            st.dataframe(runs, use_container_width=True)
            choice = st.selectbox("Run anzeigen", [r["id"] for r in runs])
            run = events.get_run(choice)
            if run:
                st.json({"status": run.get("status"), "goal": run.get("goal"),
                         "result": run.get("result")})
        else:
            st.info("Noch keine Runs.")

    with tab_approvals:
        approvals = events.list_approvals(100)
        pending = [a for a in approvals if a.get("status") == "pending"]
        st.metric("offen", len(pending))
        for item in approvals:
            with st.expander(f"{item.get('tool')} - {item.get('status')} - {item.get('id')}"):
                st.json(item)
                if item.get("status") == "pending":
                    c1, c2 = st.columns(2)
                    if c1.button("Approve", key="ok_" + item["id"]):
                        _guard_decide(item["id"], True)
                        st.rerun()
                    if c2.button("Deny", key="no_" + item["id"]):
                        _guard_decide(item["id"], False)
                        st.rerun()

    with tab_rag:
        from nexus.omega import rag

        st.metric("Dokumente", rag.count())
        query = st.text_input("RAG-Suche")
        if query.strip():
            st.dataframe(rag.search(query, 10), use_container_width=True)
        st.subheader("Zuletzt aufgenommen")
        st.dataframe(rag.recent(25), use_container_width=True)


if __name__ == "__main__":
    main()
