"""
streamlit_app.py — EvolvBI Streamlit Chat UI.

Local dev:
    streamlit run streamlit_app.py

Cloud Run:
    streamlit run streamlit_app.py --server.port 8080
"""

import asyncio
import concurrent.futures
import difflib
import os
import re
import sys
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from agents.sql_agent import build_agent, _BASE_PROMPT  # noqa: E402
from agents.improver import run_improvement_loop         # noqa: E402

# ── Prompt persistence ─────────────────────────────────────────────────────────
PROMPT_FILE = _ROOT / "current_prompt.txt"


def _load_prompt() -> str:
    return PROMPT_FILE.read_text() if PROMPT_FILE.exists() else _BASE_PROMPT


def _save_prompt(text: str) -> None:
    PROMPT_FILE.write_text(text)


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EvolvBI",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom fonts + CSS ────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1, h2, h3 { font-family: 'DM Sans', sans-serif; font-weight: 700; }
.stButton > button[kind="primary"] { background-color: #3C3489 !important; border: none; }
.stButton > button[kind="primary"]:hover { background-color: #534AB7 !important; }
.teal-badge { color: #1D9E75; font-weight: 600; }
.coral-badge { color: #D85A30; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ── Example questions ──────────────────────────────────────────────────────────
EXAMPLE_QUESTIONS = [
    ("📊 Revenue", "Which mall had the highest revenue last month?"),
    ("🏆 Categories", "What are the top 5 categories by total sales?"),
    ("👥 Customers", "How many unique customers visited Kanyon in 2022?"),
    ("📉 Slow", "Which tenant had the lowest revenue in 2022 at Forum Istanbul?"),
    ("📅 Seasonal", "Which month had the highest sales across all malls in 2022?"),
    ("🧮 Basket", "What is the average basket size per category?"),
]


# ── ADK runner — session-state based (not @st.cache_resource) ─────────────────
# Stored in st.session_state so it can be rebuilt live when prompt is updated.

def _get_user_id() -> str:
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())[:8]
    return st.session_state.user_id


def _get_runner(force_prompt: str | None = None) -> tuple[Runner, InMemorySessionService]:
    """Return (runner, svc). Rebuilds if force_prompt is given or not yet initialised."""
    if "runner" not in st.session_state or force_prompt is not None:
        prompt = force_prompt or _load_prompt()
        agent = build_agent(prompt)
        svc = InMemorySessionService()
        r = Runner(agent=agent, app_name="evolvbi", session_service=svc)
        st.session_state.runner = r
        st.session_state._svc = svc
        st.session_state.current_prompt = prompt
    return st.session_state.runner, st.session_state._svc


def _get_session_id() -> str:
    _, svc = _get_runner()
    if "adk_session_id" not in st.session_state:
        session = asyncio.run(
            svc.create_session(app_name="evolvbi", user_id=_get_user_id())
        )
        st.session_state.adk_session_id = session.id
        st.session_state.messages = []
        st.session_state.last_sql = ""
        st.session_state.last_trace_id = ""
    return st.session_state.adk_session_id


def _reset() -> None:
    _, svc = _get_runner()
    session = asyncio.run(
        svc.create_session(app_name="evolvbi", user_id=_get_user_id())
    )
    st.session_state.adk_session_id = session.id
    st.session_state.messages = []
    st.session_state.last_sql = ""
    st.session_state.last_trace_id = ""
    st.session_state.pop("improvement_output", None)
    st.session_state.pop("improved_prompt", None)
    st.session_state.pop("pre_apply_pass_rate", None)
    st.rerun()


# ── Improvement helpers ────────────────────────────────────────────────────────

def _run_improvement_sync() -> str:
    """Run the async improvement loop in a thread (avoids Streamlit event loop conflict)."""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return executor.submit(asyncio.run, run_improvement_loop()).result(timeout=120)


def _apply_edits_to_prompt(base: str, edits_text: str) -> str:
    """Use Gemini to properly integrate improvement-loop edits into the base prompt.

    Avoids the naive append approach that stacks contradictory instructions.
    """
    import google.genai as genai

    _model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    client = genai.Client(
        vertexai=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "mallpulse-hackathon"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
    )
    response = client.models.generate_content(
        model=_model,
        contents=f"""You are rewriting a system prompt to incorporate proposed improvements.
Do not append — rewrite the relevant sections so the improvements are naturally integrated.
Return ONLY the complete rewritten prompt. Keep it concise and non-contradictory.

CURRENT PROMPT:
{base}

PROPOSED IMPROVEMENTS:
{edits_text}""",
    )
    return response.text.strip()


# ── Diff renderer ──────────────────────────────────────────────────────────────
def _render_prompt_diff(old: str, new: str) -> str:
    """Return HTML showing old text in red strikethrough, new text in green."""
    diff = list(difflib.ndiff(old.splitlines(keepends=True), new.splitlines(keepends=True)))
    html_lines = []
    for line in diff:
        escaped = (
            line[2:].rstrip("\n")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        if line.startswith("- "):
            html_lines.append(
                f'<span style="background:#3d0000;color:#ff6b6b;text-decoration:line-through;'
                f'display:block;padding:1px 6px;font-family:monospace;white-space:pre-wrap">'
                f'{escaped}</span>'
            )
        elif line.startswith("+ "):
            html_lines.append(
                f'<span style="background:#003320;color:#1D9E75;display:block;padding:1px 6px;'
                f'font-family:monospace;white-space:pre-wrap">{escaped}</span>'
            )
        elif line.startswith("  "):
            html_lines.append(
                f'<span style="color:#888;display:block;padding:1px 6px;'
                f'font-family:monospace;white-space:pre-wrap">{escaped}</span>'
            )
    return "<div>" + "".join(html_lines) + "</div>"


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

    # ── Improvement loop ───────────────────────────────────────────────────────
    st.markdown("### 🔄 Improvement Loop")
    st.caption("Reads Phoenix failure traces · proposes prompt edits")

    if st.button("Run improvement loop", use_container_width=True, type="primary"):
        with st.spinner("Analysing failures in Phoenix…"):
            try:
                output = _run_improvement_sync()
                st.session_state.improvement_output = output
                # Pre-compute the rewritten prompt (shown in diff, applied on confirm)
                st.session_state.improved_prompt = _apply_edits_to_prompt(
                    st.session_state.get("current_prompt", _load_prompt()), output
                )
            except Exception as e:
                st.session_state.improvement_output = f"Error: {e}"
                st.session_state.improved_prompt = None

    if "improvement_output" in st.session_state:
        with st.expander("Proposed edits", expanded=True):
            st.markdown(st.session_state.improvement_output)

        if st.session_state.get("improved_prompt"):
            with st.expander("Prompt diff", expanded=True):
                diff_html = _render_prompt_diff(
                    st.session_state.get("current_prompt", _load_prompt()),
                    st.session_state["improved_prompt"],
                )
                st.markdown(diff_html, unsafe_allow_html=True)

            # ── Apply & Rebuild button ─────────────────────────────────────────
            if st.button("✅ Apply & Rebuild Agent", use_container_width=True, type="primary"):
                new_prompt = st.session_state["improved_prompt"]
                _save_prompt(new_prompt)
                # Invalidate session so next query uses the new runner
                st.session_state.pop("adk_session_id", None)
                _get_runner(force_prompt=new_prompt)
                st.success("Agent rebuilt with improved prompt. Next query uses the new instructions.")
                st.session_state.pop("improvement_output", None)
                st.session_state.pop("improved_prompt", None)
                st.rerun()

    st.divider()
    if st.button("🗑️ Clear conversation", use_container_width=True):
        _reset()

    st.divider()
    phoenix_base = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").replace("/v1/traces", "")
    if phoenix_base:
        st.markdown(f"[Open Phoenix traces ↗]({phoenix_base}/projects/evolvbi)")
    else:
        st.caption("_Set PHOENIX\\_COLLECTOR\\_ENDPOINT to enable trace links._")


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
                if msg.get("trace_id") and phoenix_base:
                    trace_url = f"{phoenix_base}/projects/evolvbi/traces/{msg['trace_id']}"
                    st.markdown(f"[View trace in Phoenix ↗]({trace_url})")
                elif msg.get("trace_id"):
                    st.caption("Phoenix endpoint not configured — set PHOENIX_COLLECTOR_ENDPOINT to enable trace links.")

prompt = st.chat_input("Ask about a mall, tenant, category, or time period…")
if not prompt and "pending_prompt" in st.session_state:
    prompt = st.session_state.pop("pending_prompt")

if prompt:
    _get_session_id()
    runner, _ = _get_runner()

    # Guard against double-render on sidebar click mid-conversation
    msgs = st.session_state.get("messages", [])
    if not msgs or msgs[-1].get("content") != prompt or msgs[-1].get("role") != "user":
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
                user_id=_get_user_id(),
                session_id=st.session_state.adk_session_id,
                new_message=Content(parts=[Part(text=prompt)], role="user"),
            ):
                calls = (
                    event.get_function_calls()
                    if hasattr(event, "get_function_calls") else []
                )
                if calls:
                    tool_names = ", ".join(f"`{c.name}`" for c in calls)
                    status_slot.caption(f"⚙️ Calling {tool_names}…")
                    for call in calls:
                        if call.name == "query_warehouse" and hasattr(call, "args"):
                            last_sql = (call.args or {}).get("sql", "") or str(call.args)

                if hasattr(event, "invocation_id") and event.invocation_id:
                    trace_id = event.invocation_id

                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            full_text += part.text
                            text_slot.markdown(full_text + " ▌")

        except Exception as exc:
            err = str(exc).lower()
            if "quota" in err or "rate" in err:
                full_text = "⚠️ Query limit reached — please try again in a moment."
            elif "bigquery" in err:
                full_text = "⚠️ Data warehouse is temporarily unavailable."
            else:
                full_text = "⚠️ Something went wrong. Try rephrasing your question."

        status_slot.empty()
        text_slot.markdown(full_text or "_(No response)_")

        if last_sql:
            with st.expander("Show reasoning (SQL + trace)", expanded=False):
                st.code(last_sql, language="sql")
                if trace_id and phoenix_base:
                    trace_url = f"{phoenix_base}/projects/evolvbi/traces/{trace_id}"
                    st.markdown(f"[View trace in Phoenix ↗]({trace_url})")
                elif trace_id:
                    st.caption("Set PHOENIX_COLLECTOR_ENDPOINT to enable trace links.")

        st.session_state.last_sql = last_sql
        st.session_state.last_trace_id = trace_id

    st.session_state.messages.append({
        "role": "assistant",
        "content": full_text,
        "sql": last_sql,
        "trace_id": trace_id,
    })
