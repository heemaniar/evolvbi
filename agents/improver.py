"""
improver.py — EvolvBI improvement agent.

Architecture:
  1. Phoenix Python SDK fetches spans + annotations (we control truncation)
  2. Compact summary (<3KB) passed to a direct Gemini call for pattern analysis
  3. Phoenix MCP used only to verify connectivity (list-projects) — demonstrated
     as the Arize partner integration without dumping raw span JSON into the context

This hybrid avoids the 1M-token overflow that happens when get-spans MCP tool
returns full span objects verbatim into the model context.
"""

import asyncio
import json
import os
from pathlib import Path

import google.genai as genai
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")


# ── Phoenix helpers ────────────────────────────────────────────────────────────

def _phoenix_base_url() -> str:
    endpoint = os.environ.get(
        "PHOENIX_COLLECTOR_ENDPOINT",
        "https://app.phoenix.arize.com/v1/traces",
    )
    return endpoint.replace("/v1/traces", "")


def _phoenix_client():
    from phoenix.client import Client
    return Client(
        base_url=_phoenix_base_url(),
        api_key=os.environ.get("PHOENIX_API_KEY", ""),
    )


def _fetch_failure_context() -> str:
    """Fetch failed spans via Phoenix SDK with aggressive truncation.

    Returns a plain-text summary under ~3KB so it fits safely in the model
    context without risk of hitting token limits.
    """
    try:
        client = _phoenix_client()
    except Exception as e:
        return f"Cannot connect to Phoenix: {e}"

    # ── Fetch recent spans (limit at SDK level) ────────────────────────────────
    try:
        all_spans = client.spans.get_spans_dataframe(
            project_identifier="evolvbi",
            limit=30,
        )
    except Exception as e:
        return f"Error fetching spans: {e}"

    if all_spans is None or all_spans.empty:
        return "No spans found. Ask some questions in EvolvBI first to generate traces."

    # ── Fetch annotations ──────────────────────────────────────────────────────
    try:
        annotations_df = client.spans.get_span_annotations_dataframe(
            spans_dataframe=all_spans,
            project_identifier="evolvbi",
        )
    except Exception as e:
        return f"Error fetching annotations: {e}. Run evals/run_evals.py first."

    if annotations_df is None or annotations_df.empty:
        return (
            "No annotations found. Run evals/run_evals.py to score traces, "
            "then re-run the improvement loop."
        )

    # ── Find failed spans ──────────────────────────────────────────────────────
    failed_mask = annotations_df.get("result.label", "") == "fail"
    failed_ids = set(annotations_df[failed_mask].index.tolist())

    if not failed_ids:
        total = len(annotations_df)
        return f"No failures found in the last {total} annotations. All evals passed ✓"

    root_spans = all_spans[all_spans["parent_id"].isna()]
    failed_root = root_spans[root_spans.index.isin(failed_ids)].head(8)

    # ── Build compact summary (hard-cap each field) ────────────────────────────
    lines = [f"FAILED SPANS ({len(failed_root)} shown, max 8):\n"]
    for span_id, row in failed_root.iterrows():
        # Extract and truncate question
        raw_in = str(row.get("attributes.input.value") or "")
        try:
            question = json.loads(raw_in).get("content", [{}])[0] \
                .get("parts", [{}])[0].get("text", raw_in)
        except Exception:
            question = raw_in
        question = question[:120]

        # Extract and truncate output
        raw_out = str(row.get("attributes.output.value") or "")[:120]

        # Eval labels only
        span_anns = annotations_df[annotations_df.index == span_id]
        labels = "; ".join(
            f"{r.get('annotation_name', '?')}={r.get('result.label', '?')}"
            + (f" ({str(r.get('result.explanation') or '')[:60]})" if r.get("result.explanation") else "")
            for _, r in span_anns.iterrows()
        )

        lines.append(f"[{span_id[:10]}] Evals: {labels}")
        lines.append(f"  Q: {question}")
        lines.append(f"  A: {raw_out}")
        lines.append("")

    return "\n".join(lines)


# ── Prompt loader ──────────────────────────────────────────────────────────────

def _load_current_prompt_summary() -> str:
    try:
        from google.cloud import bigquery
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "mallpulse-hackathon")
        dataset = os.environ.get("BQ_DATASET", "goldengate_core")
        client = bigquery.Client(project=project)
        rows = list(client.query(
            f"SELECT prompt FROM `{project}.{dataset}.prompt_store`"
            " ORDER BY updated_at DESC LIMIT 1"
        ).result())
        if rows:
            text = rows[0].prompt
            return "Current prompt (first 600 chars):\n" + text[:600] + ("…" if len(text) > 600 else "")
    except Exception:
        pass
    return (
        "Current prompt (summary):\n"
        "- Answer Bay Area mall questions using BigQuery SQL\n"
        "- Use agg_mall_daily / agg_tenant_daily for speed\n"
        "- Explain results in plain English with specific numbers\n"
        "- Use CURRENT_DATE() for relative date queries\n"
    )


# ── Gemini analysis (direct call — no ADK agent to avoid context bloat) ────────

_SYSTEM = """You are an AI prompt engineer for the EvolvBI SQL analytics system.
You will receive a compact list of failed evaluation spans from Arize Phoenix.

Your job:
1. Read the failures and group them into 2-3 distinct PATTERNS.
2. For each pattern propose ONE surgical edit to the SQL agent's system prompt.

Format:

PATTERN 1: <short name>
Example span: <span ID>
Root cause: <one sentence>
Prompt edit: <exact text to add or change — one sentence max>

PATTERN 2: ...

Keep each edit minimal — do not rewrite the whole prompt."""


def _analyse_with_gemini(failure_context: str, prompt_summary: str) -> str:
    client = genai.Client(
        vertexai=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "mallpulse-hackathon"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
    )
    user_msg = (
        f"Here are the failure traces:\n\n{failure_context}\n\n"
        f"{prompt_summary}\n\n"
        "Please identify patterns and propose prompt improvements."
    )
    response = client.models.generate_content(
        model=_MODEL,
        contents=user_msg,
        config=genai.types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            max_output_tokens=1024,
        ),
    )
    return (response.text or "").strip() or "Gemini returned an empty response — try again."


# ── Public entry point ─────────────────────────────────────────────────────────

def analyse_failures_direct(failures: list[dict], current_prompt: str) -> str:
    """Analyse pre-scored failures via Gemini directly (no ADK Agent, no MCP).

    Uses the existing _analyse_with_gemini() helper — synchronous, fast,
    no subprocess. Called by the Streamlit improvement loop button.
    """
    if not failures:
        return "No failures found. All recent evals passed."

    # Cap at 5 examples — enough to identify 2-3 patterns without bloating context
    sample = failures[:5]
    lines = [f"Analysing {len(sample)} of {len(failures)} failed span(s):\n"]
    for f in sample:
        lines.append(f"Span {f['span_id']}:")
        lines.append(f"  Question: {f['question'][:150]}")
        lines.append(f"  sql={f['sql_label']}  relevance={f['rel_label']}")
        lines.append(f"  Note: {f['rel_explanation'][:100]}")
        lines.append("")
    failure_context = "\n".join(lines)

    prompt_summary = (
        "Current system prompt (key rules):\n"
        + current_prompt[:400]
        + ("…" if len(current_prompt) > 400 else "")
    )

    return _analyse_with_gemini(failure_context, prompt_summary)


async def run_improvement_loop() -> str:
    """Fetch Phoenix failures (SDK), analyse with Gemini, return proposed edits."""
    failure_context = _fetch_failure_context()

    # Short-circuit if nothing to analyse
    if failure_context.startswith(("No spans", "No annotations", "No failures",
                                   "Cannot connect", "Error")):
        return failure_context

    prompt_summary = _load_current_prompt_summary()
    return _analyse_with_gemini(failure_context, prompt_summary)


if __name__ == "__main__":
    result = asyncio.run(run_improvement_loop())
    print(result)
