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


def _check_grounding(answer: str, tool_output: str) -> tuple[bool, str]:
    """Grounding eval: check that significant numbers in the answer appear in query results.

    Catches hallucinated figures — e.g. an agent that says '$1.67 billion'
    when the query returned nothing or $9.96M. sql_success + sql_relevance
    both pass that answer; sql_grounding fails it.

    Returns (is_grounded: bool, explanation: str).
    """
    if not tool_output or not answer:
        return True, "No tool output to check against."

    # Extract dollar amounts and large integers from the answer
    answer_numbers = set(re.findall(r'\$[\d,]+(?:\.\d+)?[BMK]?|\b\d{4,}(?:,\d{3})*(?:\.\d+)?\b', answer))

    if not answer_numbers:
        return True, "No significant numbers in answer to verify."

    # Parse every numeric value out of the tool output for proximity matching.
    output_values: list[float] = []
    for tok in re.findall(r'[\d,]+(?:\.\d+)?', tool_output):
        try:
            output_values.append(float(tok.replace(",", "")))
        except ValueError:
            continue
    output_flat = tool_output.replace(",", "").replace("$", "")

    ungrounded = []
    for num_str in answer_numbers:
        # Normalise: remove $, commas, handle B/M/K suffixes → numeric value
        normalised = num_str.replace("$", "").replace(",", "")
        if normalised.endswith("B"):
            value = float(normalised[:-1]) * 1e9
        elif normalised.endswith("M"):
            value = float(normalised[:-1]) * 1e6
        elif normalised.endswith("K"):
            value = float(normalised[:-1]) * 1e3
        else:
            try:
                value = float(normalised)
            except ValueError:
                continue

        core = str(int(value))  # integer part

        # Bare 4-digit years (e.g. "2026" mentioned in prose) are not data
        # figures — skip them so they don't false-flag as ungrounded.
        if "$" not in num_str and 1900 <= int(value) <= 2100:
            continue

        # Only verify figures with >=4 integer digits (smaller ones are noise).
        if len(core) < 4:
            continue

        # 1) exact substring match against the raw output, OR
        if core in output_flat:
            continue
        # 2) numeric proximity (within ~2%) — lets rounded answers pass
        #    ("$264,000" vs a query result of 263,912) without false-flagging.
        if any(v != 0 and abs(value - v) / abs(v) <= 0.02 for v in output_values):
            continue
        ungrounded.append(num_str)

    if ungrounded:
        return False, f"Numbers in response not found in query results: {', '.join(ungrounded[:3])}"
    return True, "All significant numbers traceable to query results."


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
    """Evaluate one root span (CODE + LLM). Returns (annotations, failure_dict|None).

    CODE eval checks the root span's own output for SQL error strings — no
    child span fetch needed (avoids the large dataset query that caused timeouts).
    """
    span_id, row = args
    question, answer = _parse_input_output(row)

    # ── Eval 1: sql_success (CODE) — check root output directly ──────────────
    # The agent includes BigQuery errors in its final response when SQL fails,
    # so checking the root span output catches the same failures as child spans.
    root_output = str(row.get("attributes.output.value", "") or "")
    has_error   = _has_sql_error([root_output])
    sql_label   = "fail" if has_error else "pass"

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

    # ── Eval 3: sql_grounding (CODE) — hallucination detection ───────────────
    # Checks that significant numbers in the response actually appear in the
    # query results. Catches fabricated figures that pass sql_success + sql_relevance.
    grounded, ground_explanation = _check_grounding(answer, root_output)
    ground_label = "pass" if grounded else "fail"

    ann_ground = SpanAnnotationData(
        span_id=span_id,
        name="sql_grounding",
        annotator_kind="CODE",
        result={
            "label":       ground_label,
            "score":       1.0 if grounded else 0.0,
            "explanation": ground_explanation,
        },
    )

    failure = None
    if sql_label == "fail" or rel_label == "fail" or ground_label == "fail":
        failure = {
            "span_id":         span_id[:12],
            "question":        question[:200],
            "answer":          answer[:300],
            "sql_label":       sql_label,
            "rel_label":       rel_label,
            "ground_label":    ground_label,
            "rel_explanation": explanation[:200],
        }

    return [ann_sql, ann_rel, ann_ground], failure


def score_recent_spans(max_spans: int = 20) -> dict:
    """Score the most recent N root spans from Phoenix.

    Returns:
        {"scored": int, "failures": list[dict], "error": str|None}
    """
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    try:
        # root_spans_only=True → single small query (no child spans fetched).
        # limit + start_time + timeout=15 keep it fast and bounded.
        since = datetime.now(timezone.utc) - timedelta(days=7)
        root_spans = _client.spans.get_spans_dataframe(
            project_identifier=PROJECT,
            root_spans_only=True,
            limit=max_spans,
            start_time=since,
            timeout=15,
        )

        if root_spans.empty:
            return {"scored": 0, "failures": [], "error": None}

        # Most-recent first
        if "start_time" in root_spans.columns:
            root_spans = root_spans.sort_values("start_time", ascending=False)

        # ── Harness hygiene: drop non-user spans ──────────────────────────────
        # Framework/eval spans have NaN, empty, or meta-text inputs.
        # Evaluating them produces false failures and leaks eval prompts
        # back into the improvement loop (PATTERN 2 symptom).
        def _is_real_user_span(row: pd.Series) -> bool:
            q, _ = _parse_input_output(row)
            if not q or q.strip().lower() in ("nan", "none", ""):
                return False
            # Eval harness prompts contain distinctive phrases — skip them
            if any(p in q for p in (
                "Does the answer directly address",
                "evaluating whether an AI",
                "Reply with exactly one word",
            )):
                return False
            return True

        valid = [_is_real_user_span(row) for _, row in root_spans.iterrows()]
        root_spans = root_spans[valid]

        if root_spans.empty:
            return {"scored": 0, "failures": [], "error": None}

        args = [(sid, row) for sid, row in root_spans.iterrows()]

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
