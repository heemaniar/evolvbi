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
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Fonts + comprehensive UI theme ───────────────────────────────────────────
# st.html() injects <style>/<link> into the parent document head (Streamlit 1.31+)
# Do NOT use st.markdown for CSS — it strips <style> tags in newer versions.
st.html("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/icon?family=Material+Icons+Round" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20,400,0,0" rel="stylesheet">
<style>
/* ── Typography ─────────────────────────────────────────────────────────── */
html, body, [class*="css"], p, span, div, li, td, th, label,
.stMarkdown, .stChatMessage { font-family:'Inter',-apple-system,sans-serif !important; }
h1,h2,h3,h4,h5,h6 { font-family:'Plus Jakarta Sans',sans-serif !important;
    font-weight:700 !important; color:#1A1735 !important; }

/* ── App background — Lavender ──────────────────────────────────────────── */
.stApp, body { background-color:#EEEDFE !important; }
.main .block-container { padding-top:1.5rem !important; }

/* ── Sidebar — stays dark purple ────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background:linear-gradient(180deg,#150F2D 0%,#2D2156 100%) !important;
    border-right:1px solid rgba(83,74,183,0.35) !important;
}
[data-testid="stSidebar"] .block-container { padding-top:1.25rem !important; }
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown span,
[data-testid="stSidebar"] .stCaption { color:rgba(244,243,255,0.65) !important; }

/* ── Buttons — primary ──────────────────────────────────────────────────── */
.stButton>button[kind="primary"] {
    background:linear-gradient(135deg,#3C3489 0%,#534AB7 100%) !important;
    color:#fff !important; border:none !important; border-radius:10px !important;
    font-weight:600 !important; font-family:'Inter',sans-serif !important;
    box-shadow:0 2px 10px rgba(60,52,137,0.25) !important;
    transition:all 0.2s !important;
}
.stButton>button[kind="primary"]:hover {
    transform:translateY(-1px) !important;
    box-shadow:0 6px 20px rgba(83,74,183,0.4) !important;
}
/* ── Sidebar buttons (dark background) ─────────────────────────────────── */
[data-testid="stSidebar"] .stButton>button {
    background:rgba(60,52,137,0.28) !important;
    border:1px solid rgba(83,74,183,0.45) !important;
    border-radius:10px !important; color:rgba(244,243,255,0.88) !important;
    font-family:'Inter',sans-serif !important; font-size:0.85rem !important;
    transition:all 0.15s !important;
}
[data-testid="stSidebar"] .stButton>button:hover {
    background:rgba(83,74,183,0.4) !important;
    border-color:#534AB7 !important; color:#F4F3FF !important;
}
/* ── Main area buttons (lavender background) ────────────────────────────── */
.main .stButton>button {
    background:rgba(60,52,137,0.08) !important;
    border:1px solid rgba(83,74,183,0.3) !important;
    border-radius:10px !important; color:#3C3489 !important;
    font-family:'Inter',sans-serif !important; font-size:0.85rem !important;
    transition:all 0.15s !important;
}
.main .stButton>button:hover {
    background:rgba(83,74,183,0.14) !important;
    border-color:#534AB7 !important; color:#1A1735 !important;
}

/* ── Chat bubbles (on lavender) ─────────────────────────────────────────── */
[data-testid="stChatMessage"] { border-radius:14px !important; margin-bottom:6px !important; }
[data-testid="stChatMessage"][data-message-author-role="user"] {
    background:rgba(60,52,137,0.07) !important;
    border:1px solid rgba(83,74,183,0.18) !important;
}
[data-testid="stChatMessage"][data-message-author-role="assistant"] {
    background:rgba(255,255,255,0.68) !important;
    border:1px solid rgba(216,90,48,0.2) !important;
}

/* ── Chat input ─────────────────────────────────────────────────────────── */
[data-testid="stChatInputTextArea"] {
    background:rgba(255,255,255,0.82) !important;
    border:1px solid rgba(83,74,183,0.4) !important;
    border-radius:12px !important; color:#1A1735 !important;
}
[data-testid="stChatInputTextArea"]:focus-within {
    border-color:#D85A30 !important; box-shadow:0 0 0 2px rgba(216,90,48,0.15) !important;
}

/* ── Expanders ──────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background:rgba(255,255,255,0.55) !important;
    border:1px solid rgba(83,74,183,0.22) !important; border-radius:10px !important;
}
[data-testid="stExpander"] summary { color:#2D2156 !important; font-size:0.83rem !important; }

/* ── Code ───────────────────────────────────────────────────────────────── */
code,pre { background:#F0EFFE !important; border:1px solid rgba(83,74,183,0.18) !important; border-radius:7px !important; }
code { color:#1D9E75 !important; }

/* ── Diff block ─────────────────────────────────────────────────────────── */
.diff-container { border-radius:10px; overflow:hidden; border:1px solid rgba(83,74,183,0.2); }

/* ── Tables ─────────────────────────────────────────────────────────────── */
table { background:rgba(255,255,255,0.5) !important; border-radius:8px !important; }
thead tr { background:rgba(60,52,137,0.08) !important; }
th { color:#1A1735 !important; font-family:'Plus Jakarta Sans',sans-serif !important; }
td { color:#1A1735 !important; }

/* ── Dividers ───────────────────────────────────────────────────────────── */
hr { border-color:rgba(83,74,183,0.18) !important; margin:0.6rem 0 !important; }

/* ── Caption / small ────────────────────────────────────────────────────── */
.stCaption,small { color:rgba(26,23,53,0.5) !important; font-size:0.77rem !important; }

/* ── Spinner ────────────────────────────────────────────────────────────── */
.stSpinner>div { border-top-color:#D85A30 !important; }

/* ── Success message ────────────────────────────────────────────────────── */
.stSuccess { background:rgba(29,158,117,0.1) !important; border:1px solid rgba(29,158,117,0.3) !important; border-radius:10px !important; }

/* ── Tags ───────────────────────────────────────────────────────────────── */
.tag-teal  { color:#1D9E75; font-weight:600; }
.tag-coral { color:#D85A30; font-weight:600; }
.tag-purple{ color:#534AB7; font-weight:600; }

/* ── Scrollbar ──────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:#EEEDFE; }
::-webkit-scrollbar-thumb { background:#534AB7; border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:#3C3489; }

/* ── Hide Streamlit chrome ──────────────────────────────────────────────── */
#MainMenu,footer { visibility:hidden; }
</style>
""")

# ── Example questions ──────────────────────────────────────────────────────────
EXAMPLE_QUESTIONS = [
    ("↗ Revenue", "Which mall had the highest revenue last month?"),
    ("◈ Categories", "What are the top 5 categories by total sales last quarter?"),
    ("◎ Customers", "How many unique customers shopped at Valley Fair last month?"),
    ("↘ Underperform", "Which tenant had the lowest revenue at Stanford Shopping Center last quarter?"),
    ("⊕ Seasonal", "Which month had the highest sales across all malls this year?"),
    ("◌ Basket", "What is the average basket size per category?"),
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
    st.markdown("""
<div style="padding:4px 0 18px;">
  <div style="display:flex;align-items:center;gap:11px;">
    <div style="width:40px;height:40px;background:linear-gradient(135deg,#3C3489 0%,#D85A30 100%);
         border-radius:11px;display:flex;align-items:center;justify-content:center;
         font-size:20px;box-shadow:0 3px 12px rgba(216,90,48,0.4);flex-shrink:0;">✨</div>
    <div>
      <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:1.05rem;
           font-weight:800;color:#F4F3FF;line-height:1.15;letter-spacing:-0.3px;">EvolvBI</div>
      <div style="font-size:0.68rem;color:#D85A30;font-weight:600;letter-spacing:0.6px;
           font-family:'Inter',sans-serif;text-transform:uppercase;">Self-Improving Analytics</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<p style="font-size:0.72rem;color:#D85A30;font-weight:700;letter-spacing:0.9px;text-transform:uppercase;margin:0 0 8px;font-family:\'Inter\',sans-serif;">Quick questions</p>', unsafe_allow_html=True)
    for label, q in EXAMPLE_QUESTIONS:
        if st.button(label, use_container_width=True, key=f"ex_{label}"):
            st.session_state.pending_prompt = q

    st.divider()

    # ── Improvement loop ───────────────────────────────────────────────────────
    st.markdown('<p style="font-size:0.72rem;color:rgba(244,243,255,0.45);font-weight:600;letter-spacing:0.8px;text-transform:uppercase;margin:0 0 6px;font-family:\'Inter\',sans-serif;">Improvement Loop</p>', unsafe_allow_html=True)
    st.markdown('<p style="font-size:0.78rem;color:rgba(244,243,255,0.5);margin:0 0 8px;font-family:\'Inter\',sans-serif;">Reads Phoenix failure traces · proposes prompt edits</p>', unsafe_allow_html=True)

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


# ── Main header ───────────────────────────────────────────────────────────────
st.markdown("""
<div style="display:flex;align-items:center;gap:14px;padding:4px 0 6px;">
  <div style="width:52px;height:52px;background:linear-gradient(135deg,#3C3489 0%,#D85A30 100%);
       border-radius:14px;display:flex;align-items:center;justify-content:center;
       font-size:26px;box-shadow:0 4px 18px rgba(216,90,48,0.45);flex-shrink:0;">✨</div>
  <div>
    <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:2rem;font-weight:800;
         color:#1A1735;line-height:1.1;letter-spacing:-0.8px;">EvolvBI</div>
    <div style="font-size:0.78rem;color:rgba(26,23,53,0.5);font-weight:500;
         font-family:'Inter',sans-serif;letter-spacing:0.2px;">
      Ask a question · See the SQL · Watch the agent improve itself
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
st.divider()

_AVATAR = {"user": "🧑‍💼", "assistant": "📊"}

for msg in st.session_state.get("messages", []):
    with st.chat_message(msg["role"], avatar=_AVATAR.get(msg["role"])):
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

    with st.chat_message("user", avatar="🧑‍💼"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="📊"):
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
