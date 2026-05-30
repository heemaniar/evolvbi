"""
improver.py — Day 17: EvolvBI improvement agent.

Reads failure traces from Phoenix, groups them by failure pattern,
and proposes specific edits to the SQL agent's system prompt.

Run manually:
    python -c "from agents.improver import run_improvement_loop; import asyncio; asyncio.run(run_improvement_loop())"

Or triggered from Streamlit via the 'Run improvement loop' button.
"""

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from phoenix.client import Client
from phoenix.client.resources.spans import SpanAnnotationData

# ── Phoenix client ─────────────────────────────────────────────────────────────
def _phoenix_client() -> Client:
    return Client(
        base_url=os.environ["PHOENIX_COLLECTOR_ENDPOINT"].replace("/v1/traces", ""),
        api_key=os.environ["PHOENIX_API_KEY"],
    )


def _fetch_failure_context() -> str:
    """Fetch spans annotated as failed and return a structured summary for the agent."""
    client = _phoenix_client()

    all_spans = client.spans.get_spans_dataframe(project_identifier="evolvbi")
    annotations_df = client.spans.get_span_annotations_dataframe(
        spans_dataframe=all_spans,
        project_identifier="evolvbi",
    )

    if annotations_df.empty:
        return "No annotations found. Run evals/run_evals.py first."

    # Find span IDs with at least one 'fail' annotation
    # annotations_df is indexed by span_id; label is in 'result.label'
    failed_mask = annotations_df["result.label"] == "fail"
    failed_ids = set(annotations_df[failed_mask].index.tolist())

    if not failed_ids:
        return "No failure traces found. All evals passed."

    root_spans = all_spans[all_spans["parent_id"].isna()]
    failed_spans = root_spans[root_spans.index.isin(failed_ids)]

    lines = [f"Found {len(failed_spans)} failed root span(s):\n"]
    for span_id, row in failed_spans.iterrows():
        # Extract question
        raw_in = row.get("attributes.input.value", "") or ""
        question = ""
        try:
            data = json.loads(raw_in)
            parts = data.get("content", [{}])[0].get("parts", [{}])
            question = parts[0].get("text", "") if parts else ""
        except Exception:
            question = str(raw_in)[:200]

        # Get annotations for this span
        span_anns = annotations_df[annotations_df.index == span_id]
        ann_summary = "; ".join(
            f"{r['annotation_name']}={r['result.label']} "
            f"({str(r.get('result.explanation') or '')[:80]})"
            for _, r in span_anns.iterrows()
        )

        # Get tool outputs (child spans)
        child_spans = all_spans[all_spans["parent_id"] == span_id]
        tool_outputs = []
        for _, child in child_spans.iterrows():
            out = child.get("attributes.output.value", "") or ""
            if out:
                tool_outputs.append(str(out)[:300])

        lines.append(f"Span {span_id[:12]}:")
        lines.append(f"  Question: {question[:200]}")
        lines.append(f"  Evals: {ann_summary}")
        if tool_outputs:
            lines.append(f"  Tool output: {tool_outputs[0][:200]}")
        lines.append("")

    return "\n".join(lines)


# ── Improvement agent ──────────────────────────────────────────────────────────
def _load_current_prompt_summary() -> str:
    """Read the live prompt from BigQuery prompt_store (persisted across Cloud Run instances)."""
    try:
        from google.cloud import bigquery
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "mallpulse-hackathon")
        dataset = os.environ.get("BQ_DATASET", "goldengate_core")
        client = bigquery.Client(project=project)
        rows = list(client.query(
            f"SELECT prompt FROM `{project}.{dataset}.prompt_store` ORDER BY updated_at DESC LIMIT 1"
        ).result())
        if rows:
            text = rows[0].prompt
            return "Current SQL agent system prompt:\n" + text[:800] + ("…" if len(text) > 800 else "")
    except Exception:
        pass
    return """Current SQL agent system prompt (summary):
- Answer natural-language questions using BigQuery SQL about Bay Area malls
- Use aggregate tables (agg_mall_daily, agg_tenant_daily) when possible
- Explain results in plain English with specific numbers
- Never state facts not in query results
- Use CURRENT_DATE() for relative date queries
"""

_IMPROVER_INSTRUCTION = """You are an AI prompt engineer for the EvolvBI SQL analytics system.

You will be given failure traces from a SQL analytics agent — questions it answered
incorrectly, SQL that errored, or answers that didn't address the question.

Your job:
1. Read the failures carefully.
2. Group them into 2-3 distinct failure PATTERNS (e.g. "wrong aggregation", "missing date filter", "hallucinated table name").
3. For each pattern, propose ONE specific, concrete edit to the SQL agent's system prompt.

Format your response as:

PATTERN 1: <short name>
Example trace: <span ID>
Root cause: <one sentence>
Prompt edit: <exact text to add or change in the system prompt>

PATTERN 2: ...

Keep edits minimal and surgical — one sentence each. Do not rewrite the whole prompt."""


_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
_SESSION_SVC = InMemorySessionService()

improver_agent = Agent(
    name="evolvbi_improver",
    model=_MODEL,
    description="Reviews failed EvolvBI traces and proposes SQL agent prompt improvements.",
    instruction=_IMPROVER_INSTRUCTION,
)


async def run_improvement_loop() -> str:
    """Fetch failures from Phoenix, run improver agent, return proposed edits."""
    failure_context = _fetch_failure_context()

    if failure_context.startswith("No"):
        return failure_context

    message_text = (
        f"Here are the SQL agent's failure traces from Phoenix:\n\n"
        f"{failure_context}\n\n"
        f"{_load_current_prompt_summary()}\n\n"
        "Please analyse and propose prompt improvements."
    )

    session = await _SESSION_SVC.create_session(
        app_name="evolvbi_improver", user_id="analyst"
    )
    runner = Runner(
        agent=improver_agent,
        app_name="evolvbi_improver",
        session_service=_SESSION_SVC,
    )

    reply_parts = []
    async for event in runner.run_async(
        user_id="analyst",
        session_id=session.id,
        new_message=Content(role="user", parts=[Part(text=message_text)]),
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    reply_parts.append(part.text)

    return "".join(reply_parts)


if __name__ == "__main__":
    result = asyncio.run(run_improvement_loop())
    print(result)
