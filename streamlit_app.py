"""
streamlit_app.py — EvolvBI Streamlit Chat UI.

Local dev:
    streamlit run streamlit_app.py

Cloud Run:
    streamlit run streamlit_app.py --server.port 8080
"""

import asyncio
import difflib
import json
import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from agents.sql_agent import sql_agent  # noqa: E402 (after path/env setup)
from agents.improver import run_improvement_loop  # noqa: E402

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EvolvBI",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Example questions ──────────────────────────────────────────────────────────
EXAMPLE_QUESTIONS = [
    ("📊 Revenue", "Which mall had the highest revenue last month?"),
    ("🏆 Categories", "What are the top 5 categories by total sales?"),
    ("👥 Customers", "How many unique customers visited Kanyon in 2022?"),
    ("📉 Slow", "Which tenant had the lowest revenue in 2022 at Forum Istanbul?"),
    ("📅 Seasonal", "Which month had the highest sales across all malls in 2022?"),
    ("🧮 Basket", "What is the average basket size per category?"),
]

# Baseline SQL agent system prompt (used for diff visualization)
_BASE_PROMPT = """You are a retail analytics assistant for Istanbul mall managers.
You have access to a BigQuery warehouse via the query_warehouse tool.

When the user asks a business question:
1. Write a precise SQL query to answer it.
2. Run it with query_warehouse.
3. Explain the results in plain English — no jargon, no raw SQL in your reply.
4. Include specific numbers from the results to support your explanation.
5. If the question cannot be answered from the data, say so clearly.

Always use aggregate tables (agg_mall_daily, agg_tenant_daily) when possible.
Never guess — only state facts that appear in query results."""


# ── ADK runner (cached per process) ───────────────────────────────────────────
@st.cache_resource
def _get_runner() -> tuple[Runner, InMemorySessionService]:
    svc = InMemorySessionService()
    r = Runner(agent=sql_agent, app_name="evolvbi", session_service=svc)
    return r, svc


runner, _svc = _get_runner()


def _get_session_id() -> str:
    if "adk_session_id" not in st.session_state:
        session = asyncio.run(
            _svc.create_session(app_name="evolvbi", user_id="analyst")
        )
        st.session_state.adk_session_id = session.id
        st.session_state.messages = []
        st.session_state.last_sql = ""
        st.session_state.last_trace_id = ""
    return st.session_state.adk_session_id


def _reset() -> None:
    session = asyncio.run(
        _svc.create_session(app_name="evolvbi", user_id="analyst")
    )
    st.session_state.adk_session_id = session.id
    st.session_state.messages = []
    st.session_state.last_sql = ""
    st.session_state.last_trace_id = ""
    st.session_state.pop("improvement_output", None)
    st.session_state.pop("improved_prompt", None)
    st.rerun()


# ── Diff renderer ──────────────────────────────────────────────────────────────
def _render_prompt_diff(old: str, new: str) -> str:
    """Return HTML showing old text in red strikethrough, new text in green."""
    diff = list(difflib.ndiff(old.splitlines(keepends=True), new.splitlines(keepends=True)))
    html_lines = []
    for line in diff:
        if line.startswith("- "):
            escaped = line[2:].rstrip("\n").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(
                f'<span style="background:#3d0000;color:#ff6b6b;text-decoration:line-through;'
                f'display:block;padding:1px 4px;font-family:monospace;white-space:pre-wrap">'
                f'{escaped}</span>'
            )
        elif line.startswith("+ "):
            escaped = line[2:].rstrip("\n").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(
                f'<span style="background:#003d00;color:#69db7c;display:block;padding:1px 4px;'
                f'font-family:monospace;white-space:pre-wrap">{escaped}</span>'
            )
        elif line.startswith("  "):
            escaped = line[2:].rstrip("\n").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(
                f'<span style="color:#888;display:block;padding:1px 4px;'
                f'font-family:monospace;white-space:pre-wrap">{escaped}</span>'
            )
    return "<div>" + "".join(html_lines) + "</div>"


def _apply_edits_to_prompt(base: str, edits_text: str) -> str:
    """Naive: append each 'Prompt edit:' line as an extra instruction."""
    import re
    edits = re.findall(r"Prompt edit:\s*(.+?)(?=\nPATTERN|\Z)", edits_text, re.DOTALL)
    if not edits:
        return base
    additions = "\n".join(f"- {e.strip()}" for e in edits)
    return base + "\n\nAdditional instructions from improvement loop:\n" + additions


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔬 EvolvBI")
    st.caption("Self-improving SQL analytics · Istanbul Mall Data")
    st.divider()

    st.markdown("### 💡 Example questions")
    for label, q in EXAMPLE_QUESTIONS:
        if st.button(label, use_container_width=True, key=f"ex_{label}"):
            st.session_state.pending_prompt = q

    st.divider()

    # ── Improvement loop button ────────────────────────────────────────────────
    st.markdown("### 🔄 Improvement Loop")
    st.caption("Reads Phoenix failure traces · proposes prompt edits")
    if st.button("Run improvement loop", use_container_width=True, type="primary"):
        with st.spinner("Analysing failures in Phoenix…"):
            try:
                output = asyncio.run(run_improvement_loop())
                st.session_state.improvement_output = output
                st.session_state.improved_prompt = _apply_edits_to_prompt(
                    _BASE_PROMPT, output
                )
            except Exception as e:
                st.session_state.improvement_output = f"Error: {e}"
                st.session_state.improved_prompt = None

    if "improvement_output" in st.session_state:
        with st.expander("Proposed edits", expanded=True):
            st.markdown(st.session_state.improvement_output)

        if st.session_state.get("improved_prompt"):
            with st.expander("Prompt diff", expanded=False):
                diff_html = _render_prompt_diff(
                    _BASE_PROMPT, st.session_state["improved_prompt"]
                )
                st.markdown(diff_html, unsafe_allow_html=True)

    st.divider()
    if st.button("🗑️ Clear conversation", use_container_width=True):
        _reset()

    st.divider()
    phoenix_url = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").replace(
        "/v1/traces", ""
    )
    if phoenix_url:
        st.markdown(f"[Open Phoenix traces ↗]({phoenix_url}/projects/evolvbi)")


# ── Main ───────────────────────────────────────────────────────────────────────
st.markdown("# 🔬 EvolvBI")
st.caption(
    "Self-improving retail analytics · Ask a question · See the SQL · "
    "Run the improvement loop to watch the agent rewrite its own instructions."
)
st.divider()

for msg in st.session_state.get("messages", []):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("sql"):
            with st.expander("Show reasoning (SQL + trace)", expanded=False):
                st.code(msg["sql"], language="sql")
                if msg.get("trace_id"):
                    trace_url = (
                        phoenix_url + f"/projects/evolvbi/traces/{msg['trace_id']}"
                        if phoenix_url
                        else ""
                    )
                    if trace_url:
                        st.markdown(f"[View trace in Phoenix ↗]({trace_url})")

prompt = st.chat_input("Ask about a mall, tenant, category, or time period…")
if not prompt and "pending_prompt" in st.session_state:
    prompt = st.session_state.pop("pending_prompt")

if prompt:
    _get_session_id()
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status_slot = st.empty()
        text_slot = st.empty()
        full_text = ""
        last_sql = ""
        trace_id = ""

        try:
            for event in runner.run(
                user_id="analyst",
                session_id=st.session_state.adk_session_id,
                new_message=Content(parts=[Part(text=prompt)], role="user"),
            ):
                # Capture tool calls to extract SQL
                calls = (
                    event.get_function_calls()
                    if hasattr(event, "get_function_calls")
                    else []
                )
                if calls:
                    tool_names = ", ".join(f"`{c.name}`" for c in calls)
                    status_slot.caption(f"⚙️ Calling {tool_names}…")
                    for call in calls:
                        if call.name == "query_warehouse" and hasattr(call, "args"):
                            args = call.args or {}
                            last_sql = args.get("sql", "") or str(args)

                # Capture trace ID from event metadata
                if hasattr(event, "invocation_id") and event.invocation_id:
                    trace_id = event.invocation_id

                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            full_text += part.text
                            text_slot.markdown(full_text + " ▌")

        except Exception as exc:
            full_text = f"⚠️ Something went wrong: {exc}"

        status_slot.empty()
        text_slot.markdown(full_text or "_(No response)_")

        if last_sql:
            with st.expander("Show reasoning (SQL + trace)", expanded=False):
                st.code(last_sql, language="sql")
                if trace_id:
                    trace_url = (
                        phoenix_url + f"/projects/evolvbi/traces/{trace_id}"
                        if phoenix_url
                        else ""
                    )
                    if trace_url:
                        st.markdown(f"[View trace in Phoenix ↗]({trace_url})")

        st.session_state.last_sql = last_sql
        st.session_state.last_trace_id = trace_id

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": full_text,
            "sql": last_sql,
            "trace_id": trace_id,
        }
    )
