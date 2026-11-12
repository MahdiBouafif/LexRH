"""What the UI talks to. Kept separate so tests can replace it with fakes."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from lexrh.agent.audit import AuditLog, FileAuditLog
from lexrh.agent.graph import CaseAgent
from lexrh.agent.tools import InProcessTools
from lexrh.rag.answer import Assistant, build_assistant

AUDIT_PATH = Path("data/audit.jsonl")


@st.cache_resource(show_spinner="Loading the models (first start only)...")
def get_assistant() -> Assistant:
    return build_assistant()


@st.cache_resource
def get_audit() -> AuditLog:
    return FileAuditLog(AUDIT_PATH)


@st.cache_resource
def get_agent() -> CaseAgent:
    """The agent reaches the law through the MCP server, using the same assistant as the Ask tab."""
    from lexrh.mcp_server.server import build_server

    return CaseAgent(InProcessTools(build_server(get_assistant())), get_audit())
