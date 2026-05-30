"""
run_evals.py — Day 16: Phoenix eval pipeline for EvolvBI.

Two evals per root invocation span:
  1. sql_success  (CODE)  — did the SQL tool return without error?
  2. sql_relevance (LLM)  — did the answer actually address the question?
     Judge model: Gemini 2.5 Flash via Vertex AI (hackathon requires Google-only AI).

Run:
    python evals/run_evals.py

Outputs labels + scores back into Phoenix as span annotations.
"""

import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from phoenix.client import Client
from phoenix.client.resources.spans import SpanAnnotationData
from phoenix.evals import LLM

# ── Phoenix client ─────────────────────────────────────────────────────────────
_client = Client(
    base_url=os.environ["PHOENIX_COLLECTOR_ENDPOINT"].replace("/v1/traces", ""),
    api_key=os.environ["PHOENIX_API_KEY"],
)
PROJECT = "evolvbi"

# ── Gemini judge (Vertex AI — no OpenAI, per hackathon rules) ─────────────────
# gemini-3-flash-preview requires location="global"
_judge = LLM(
    provider="google",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    vertexai=True,
    project=os.environ.get("GOOGLE_CLOUD_PROJECT", "mallpulse-hackathon"),
    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
)

# ── Prompt for SQL relevance eval ─────────────────────────────────────────────
_RELEVANCE_PROMPT = """You are evaluating whether an AI assistant's answer actually
addresses the user's question.

Question: {question}

Answer: {answer}

Does the answer directly address the question with specific data or numbers?
Reply with exactly one word: PASS or FAIL.
Then on a new line, write one sentence explaining why."""


def _extract_tool_outputs(span_row: pd.Series) -> list[str]:
    """Pull all tool call outputs from a span's child tool spans."""
    return []  # child spans are fetched separately below


def _has_sql_error(tool_outputs: list[str]) -> bool:
    # "Query returned no rows" is a valid empty result — NOT a failure.
    # Only treat actual BigQuery errors or explicit Error: prefixes as failures.
    for out in tool_outputs:
        if re.search(r"(BigQuery error|^Error:)", out, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def _parse_input_output(span_row: pd.Series) -> tuple[str, str]:
    """Extract question and answer text from a root invocation span."""
    raw_in = span_row.get("attributes.input.value", "") or ""
    raw_out = span_row.get("attributes.output.value", "") or ""

    # input.value: {"new_message": {"parts": [{"text": "..."}]}, ...}
    question = ""
    answer = ""
    try:
        data = json.loads(raw_in)
        msg = data.get("new_message", {})
        parts = msg.get("parts", [])
        question = parts[0].get("text", "") if parts else ""
    except Exception:
        question = str(raw_in)[:500]

    # output.value: {"model_version": ..., "content": {"parts": [{"text": "..."}]}}
    try:
        data = json.loads(raw_out)
        content = data.get("content", {})
        if isinstance(content, dict):
            parts = content.get("parts", [{}])
            answer = parts[0].get("text", "") if parts else ""
        elif isinstance(content, list):
            parts = content[0].get("parts", [{}]) if content else [{}]
            answer = parts[0].get("text", "") if parts else ""
    except Exception:
        answer = str(raw_out)[:500]

    return question.strip(), answer.strip()


def run_evals() -> None:
    print(f"Fetching spans from Phoenix project '{PROJECT}'…")
    all_spans = _client.spans.get_spans_dataframe(project_identifier=PROJECT)
    print(f"  Total spans: {len(all_spans)}")

    # Root invocation spans only
    root_spans = all_spans[all_spans["parent_id"].isna()].copy()
    print(f"  Root spans: {len(root_spans)}")

    if root_spans.empty:
        print("No root spans found. Run app.py first to generate traces.")
        return

    annotations: list[SpanAnnotationData] = []

    for span_id, row in root_spans.iterrows():
        trace_id = row["context.trace_id"]
        question, answer = _parse_input_output(row)

        # ── Eval 1: sql_success (CODE) ────────────────────────────────────────
        # Look at child spans for tool output errors
        child_spans = all_spans[all_spans["parent_id"] == span_id]
        tool_spans = child_spans[
            child_spans["attributes.openinference.span.kind"].str.upper() == "TOOL"
        ]
        tool_outputs = [
            str(r.get("attributes.output.value", "") or "")
            for _, r in tool_spans.iterrows()
        ]

        has_error = _has_sql_error(tool_outputs)
        sql_label = "fail" if has_error else "pass"
        sql_score = 0.0 if has_error else 1.0

        annotations.append(
            SpanAnnotationData(
                span_id=span_id,
                name="sql_success",
                annotator_kind="CODE",
                result={
                    "label": sql_label,
                    "score": sql_score,
                    "explanation": "SQL returned an error string." if has_error else "SQL ran cleanly.",
                },
            )
        )

        # ── Eval 2: sql_relevance (LLM / Gemini) ─────────────────────────────
        if not question or not answer:
            relevance_label = "fail"
            relevance_score = 0.0
            explanation = "Could not extract question or answer from span."
        else:
            prompt = _RELEVANCE_PROMPT.format(question=question, answer=answer)
            try:
                raw = _judge.generate_text(prompt).strip()
                first_line = raw.split("\n")[0].strip().upper()
                explanation = raw.split("\n", 1)[1].strip() if "\n" in raw else raw
                relevance_label = "pass" if "PASS" in first_line else "fail"
                relevance_score = 1.0 if relevance_label == "pass" else 0.0
            except Exception as e:
                relevance_label = "fail"
                relevance_score = 0.0
                explanation = f"Eval error: {e}"

        annotations.append(
            SpanAnnotationData(
                span_id=span_id,
                name="sql_relevance",
                annotator_kind="LLM",
                result={
                    "label": relevance_label,
                    "score": relevance_score,
                    "explanation": explanation,
                },
            )
        )

        status = "sql_success=" + sql_label + "  sql_relevance=" + relevance_label
        q_preview = (question[:60] + "…") if len(question) > 60 else question
        print(f"  [{span_id[:12]}] {q_preview!r:65}  {status}")

    # ── Log annotations to Phoenix ────────────────────────────────────────────
    print(f"\nLogging {len(annotations)} annotations to Phoenix…")
    _client.spans.log_span_annotations(span_annotations=annotations, sync=True)
    print("Done. Open Phoenix → Tracing → evolvbi to see eval scores.")

    # ── Summary ───────────────────────────────────────────────────────────────
    passes = sum(1 for a in annotations if a.get("result", {}).get("label") == "pass")
    fails = sum(1 for a in annotations if a.get("result", {}).get("label") == "fail")
    print(f"\nSummary: {passes} pass  |  {fails} fail  |  {len(annotations)} total")


if __name__ == "__main__":
    run_evals()
