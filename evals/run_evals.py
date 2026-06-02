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


def _eval_single_span(args: tuple) -> tuple[list[SpanAnnotationData], dict | None]:
    """Evaluate one root span (CODE + LLM). Returns (annotations, failure_dict|None)."""
    span_id, row, all_spans = args
    question, answer = _parse_input_output(row)

    # ── Eval 1: sql_success (CODE) ────────────────────────────────────────────
    child_spans = all_spans[all_spans["parent_id"] == span_id]
    tool_spans  = child_spans[
        child_spans["attributes.openinference.span.kind"].str.upper() == "TOOL"
    ]
    tool_outputs = [
        str(r.get("attributes.output.value", "") or "")
        for _, r in tool_spans.iterrows()
    ]
    has_error  = _has_sql_error(tool_outputs)
    sql_label  = "fail" if has_error else "pass"

    ann_sql = SpanAnnotationData(
        span_id=span_id,
        name="sql_success",
        annotator_kind="CODE",
        result={
            "label": sql_label,
            "score": 0.0 if has_error else 1.0,
            "explanation": "SQL returned an error." if has_error else "SQL ran cleanly.",
        },
    )

    # ── Eval 2: sql_relevance (LLM / Gemini) ─────────────────────────────────
    if not question or not answer:
        rel_label   = "fail"
        rel_score   = 0.0
        explanation = "Could not extract question or answer."
    else:
        prompt = _RELEVANCE_PROMPT.format(
            question=question[:400], answer=answer[:600]
        )
        try:
            raw        = _judge.generate_text(prompt).strip()
            first_line = raw.split("\n")[0].strip().upper()
            explanation = raw.split("\n", 1)[1].strip() if "\n" in raw else raw
            rel_label  = "pass" if "PASS" in first_line else "fail"
            rel_score  = 1.0 if rel_label == "pass" else 0.0
        except Exception as e:
            rel_label   = "fail"
            rel_score   = 0.0
            explanation = f"Eval error: {e}"

    ann_rel = SpanAnnotationData(
        span_id=span_id,
        name="sql_relevance",
        annotator_kind="LLM",
        result={"label": rel_label, "score": rel_score, "explanation": explanation},
    )

    failure = None
    if sql_label == "fail" or rel_label == "fail":
        failure = {
            "span_id":    span_id[:12],
            "question":   question[:200],
            "answer":     answer[:300],
            "sql_label":  sql_label,
            "rel_label":  rel_label,
            "rel_explanation": explanation[:200],
        }

    return [ann_sql, ann_rel], failure


def score_recent_spans(max_spans: int = 20) -> dict:
    """Score the most recent N root spans from Phoenix.

    Returns:
        {"scored": int, "failures": list[dict], "error": str|None}
    """
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    try:
        # Fetch recent spans only — hard cap + 7-day window + 30s timeout
        # prevents hanging on large Phoenix projects.
        since = datetime.now(timezone.utc) - timedelta(days=7)
        all_spans = _client.spans.get_spans_dataframe(
            project_identifier=PROJECT,
            limit=max_spans * 8,   # enough to cover root + child spans
            start_time=since,
            timeout=30,
        )

        root_spans = all_spans[all_spans["parent_id"].isna()].copy()

        if root_spans.empty:
            return {"scored": 0, "failures": [], "error": None}

        # Most-recent first, cap at max_spans
        if "start_time" in root_spans.columns:
            root_spans = root_spans.sort_values("start_time", ascending=False)
        root_spans = root_spans.head(max_spans)

        args = [(sid, row, all_spans) for sid, row in root_spans.iterrows()]

        all_annotations: list[SpanAnnotationData] = []
        failures: list[dict] = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            for anns, failure in executor.map(_eval_single_span, args):
                all_annotations.extend(anns)
                if failure:
                    failures.append(failure)

        # Post annotations back to Phoenix
        if all_annotations:
            _client.spans.log_span_annotations(
                span_annotations=all_annotations, sync=True
            )

        return {"scored": len(root_spans), "failures": failures, "error": None}

    except Exception as e:
        return {"scored": 0, "failures": [], "error": str(e)}


def run_evals() -> None:
    """CLI entry point — scores all root spans and prints a summary."""
    print(f"Fetching spans from Phoenix project '{PROJECT}'…")
    result = score_recent_spans(max_spans=50)

    if result["error"]:
        print(f"Error: {result['error']}")
        return

    scored   = result["scored"]
    failures = result["failures"]
    passes   = scored - len(failures)
    print(f"\nScored {scored} spans — {passes} pass | {len(failures)} fail")
    for f in failures:
        print(f"  FAIL [{f['span_id']}] sql={f['sql_label']} rel={f['rel_label']}  Q: {f['question'][:60]}")
    print("\nAnnotations posted to Phoenix.")


if __name__ == "__main__":
    run_evals()
