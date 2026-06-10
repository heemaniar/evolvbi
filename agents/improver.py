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


# ── Arize Phoenix MCP server (the partner MCP integration) ──────────────────────
# The improvement loop reads its failure data by calling the official Phoenix MCP
# server (@arizeai/phoenix-mcp) at runtime over stdio — get-spans + get-span-
# annotations — not just the SDK. This is the Arize-track partner MCP requirement,
# genuinely invoked when the loop runs. Falls back to the SDK if the MCP server
# (Node/npx) is unavailable, so the demo never hard-fails.
_PHOENIX_PROJECT = "evolvbi"


def _mcp_server_params():
    from mcp import StdioServerParameters
    return StdioServerParameters(
        command="npx",
        args=[
            "-y", "@arizeai/phoenix-mcp@latest",
            "--baseUrl", _phoenix_base_url(),
            "--apiKey", os.environ.get("PHOENIX_API_KEY", ""),
        ],
    )


def _q_from_attrs(attrs: dict) -> str:
    """Best-effort question text from a span's attributes (MCP shape varies)."""
    raw = ""
    if isinstance(attrs, dict):
        raw = (attrs.get("input", {}) or {}).get("value", "") if isinstance(attrs.get("input"), dict) else ""
        raw = raw or attrs.get("input.value", "") or ""
    try:
        d = json.loads(raw)
        parts = d.get("content", [{}])[0].get("parts", [{}]) if isinstance(d.get("content"), list) else d.get("new_message", {}).get("parts", [{}])
        return (parts[0].get("text", "") or raw)[:200]
    except Exception:
        return str(raw)[:200]


async def _mcp_fetch_failures_async(limit: int = 40):
    """Connect to the Phoenix MCP server and return (failures, spans_read, note)."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_mcp_server_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            def _text(res):
                return "".join(getattr(c, "text", "") for c in res.content)

            spans_res = await session.call_tool(
                "get-spans",
                {"project_identifier": _PHOENIX_PROJECT, "limit": limit},
            )
            data = json.loads(_text(spans_res))
            spans = data.get("spans", data) if isinstance(data, dict) else data
            spans = spans or []
            roots = [s for s in spans if not s.get("parent_id")]
            root_ids = [(s.get("context") or {}).get("span_id") for s in roots]
            root_ids = [i for i in root_ids if i]
            if not root_ids:
                return [], len(spans), f"Phoenix MCP: read {len(spans)} spans, no root spans yet."

            ann_res = await session.call_tool(
                "get-span-annotations",
                {"project_identifier": _PHOENIX_PROJECT, "span_ids": root_ids},
            )
            anns = json.loads(_text(ann_res))
            anns = anns.get("annotations", anns) if isinstance(anns, dict) else anns
            anns = anns or []

            by_span: dict[str, dict] = {}
            for a in anns:
                sid = a.get("span_id") or a.get("spanId") or ""
                name = a.get("name") or a.get("annotation_name") or "?"
                res = a.get("result") or {}
                label = a.get("label") or res.get("label")
                expl = a.get("explanation") or res.get("explanation") or ""
                d = by_span.setdefault(sid, {"labels": {}, "expl": ""})
                d["labels"][name] = label
                if label == "fail" and expl and not d["expl"]:
                    d["expl"] = expl

            attrs_by_id = {(s.get("context") or {}).get("span_id"): (s.get("attributes") or {}) for s in roots}
            failures = []
            for sid, info in by_span.items():
                if "fail" not in info["labels"].values():
                    continue
                failures.append({
                    "span_id": sid,
                    "question": _q_from_attrs(attrs_by_id.get(sid, {})) or "(question unavailable via MCP)",
                    "sql_label": info["labels"].get("sql_success", "?"),
                    "rel_label": info["labels"].get("sql_relevance", "?"),
                    "ground_label": info["labels"].get("sql_grounding", "?"),
                    "rel_explanation": info["expl"],
                })
            return failures, len(spans), f"Phoenix MCP: read {len(spans)} spans, {len(failures)} failures."


def fetch_failures_via_mcp(limit: int = 40):
    """Sync wrapper: returns (failures:list[dict], note:str). Empty list on any error."""
    try:
        failures, n, note = asyncio.run(_mcp_fetch_failures_async(limit))
        return failures, note
    except Exception as e:
        return [], f"Phoenix MCP unavailable ({type(e).__name__}: {str(e)[:80]}); using SDK fallback."


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
    # Fold system instruction into the message — more compatible with
    # gemini-3-flash-preview on Vertex AI global endpoint than GenerateContentConfig
    # system_instruction (which returns empty candidates on this model).
    full_msg = (
        f"{_SYSTEM}\n\n"
        f"---\n\n"
        f"FAILURE TRACES:\n{failure_context}\n\n"
        f"{prompt_summary}\n\n"
        "Identify patterns and propose prompt improvements."
    )
    response = client.models.generate_content(
        model=_MODEL,
        contents=full_msg,
        config=genai.types.GenerateContentConfig(
            max_output_tokens=2048,
        ),
    )
    text = (response.text or "").strip()
    if not text and response.candidates:
        reason = getattr(response.candidates[0], "finish_reason", "unknown")
        return f"⚠️ Gemini response empty (finish_reason: {reason}). Try again."
    return text or "⚠️ Gemini returned no response. Try again."


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
    """Read failures via the Phoenix MCP server (partner integration), analyse with
    Gemini, return proposed edits. Falls back to the Phoenix SDK if the MCP server
    is unavailable so the loop still runs."""
    prompt_summary = _load_current_prompt_summary()

    # Primary path: the Arize Phoenix MCP server, called at runtime.
    try:
        failures, n_spans, note = await _mcp_fetch_failures_async(40)
        print(note)
        if failures:
            return analyse_failures_direct(failures, prompt_summary)
        if n_spans:  # MCP worked, just no failures scored yet
            return ("No failures found via Phoenix MCP. Run evals/run_evals.py to "
                    "score recent traces, then re-run the improvement loop.")
    except Exception as e:
        print(f"Phoenix MCP unavailable ({type(e).__name__}: {e}); falling back to SDK.")

    # Fallback: Phoenix SDK.
    failure_context = _fetch_failure_context()
    if failure_context.startswith(("No spans", "No annotations", "No failures",
                                   "Cannot connect", "Error")):
        return failure_context
    return _analyse_with_gemini(failure_context, prompt_summary)


if __name__ == "__main__":
    result = asyncio.run(run_improvement_loop())
    print(result)
