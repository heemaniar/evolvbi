"""
sql_agent.py — EvolvBI SQL agent with Arize Phoenix tracing.

Answers natural-language questions from non-technical mall analysts
by generating and running BigQuery SQL, then explaining the results.

Exports build_agent() so streamlit_app.py can rebuild the agent live
whenever the improvement loop applies prompt edits.
"""

import os
from datetime import date as _date

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

from phoenix.otel import register
from openinference.instrumentation.google_adk import GoogleADKInstrumentor

from google.adk.agents import Agent

from tools.bigquery_tools import query_warehouse, forecast_mall_revenue, SCHEMA

# ── Model — Gemini 3 (global) with 2.5 Flash fallback ────────────────────────
_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

# Today's date — injected into prompts so the agent never uses its knowledge cutoff
_TODAY = _date.today().isoformat()

# ── Phoenix tracing — lazy init ───────────────────────────────────────────────
# Deferred until first call to avoid network round-trip at import time,
# which was causing slow cold starts on Cloud Run.
_PHOENIX_ENDPOINT = os.environ.get(
    "PHOENIX_COLLECTOR_ENDPOINT",
    "https://app.phoenix.arize.com/v1/traces",
)

_tracer_provider = None


def _get_tracer_provider():
    """Lazily initialise Phoenix tracing on first use."""
    global _tracer_provider
    if _tracer_provider is None:
        _tracer_provider = register(
            project_name="evolvbi",
            endpoint=_PHOENIX_ENDPOINT,
        )
        GoogleADKInstrumentor().instrument(tracer_provider=_tracer_provider)
    return _tracer_provider


# Keep tracer_provider as a module-level name for improver.py imports,
# but resolve lazily so import itself is instant.
class _LazyTracerProvider:
    def __getattr__(self, name):
        return getattr(_get_tracer_provider(), name)


tracer_provider = _LazyTracerProvider()

# ── Base system prompt (shared with streamlit_app for diff visualization) ─────
_BASE_PROMPT = f"""You are a retail analytics assistant for Bay Area shopping mall analysts.
You have access to a BigQuery warehouse (goldengate_core) via the query_warehouse tool.

⚠️ All data is completely synthetic and generated for demonstration purposes only.
⚠️ TODAY'S DATE IS {_TODAY}. ALL relative date references ("last month", "last quarter",
   "this year", "recent") MUST be calculated from {_TODAY} using CURRENT_DATE().
   NEVER treat any year before {_TODAY[:4]} as "current" or "recent".

When the user asks a business question:
1. Write a precise SQL query to answer it.
2. Run it with query_warehouse.
3. Explain the results in plain English — no jargon, no raw SQL in your reply.
4. Include specific numbers from the results to support your explanation.
5. If the question cannot be answered from the data, attempt the closest reasonable interpretation and state your assumption clearly.

Always use aggregate tables (agg_mall_daily, agg_tenant_daily) when possible.
Never guess — only state facts that appear in query results.
Strictly adhere to the provided schema; never reference tables or columns not listed below.
Use CURRENT_DATE() for relative date queries ("last 30 days", "this year", etc.).
Active tenants filter: effective_to >= CURRENT_DATE(). Never use effective_to IS NULL.
All monetary values are in USD ($). Date range: Jan 2020 – yesterday.

## Critical accuracy rules
- **No-volunteer rule**: If a query returns no data for the requested period, say
  "No data for [period]" and STOP. NEVER substitute or volunteer figures from a
  different period — do not offer 2020 data when asked about 2019.
- **Unique customers**: always COUNT(DISTINCT customer_id) from fact_transactions.
  Never SUM(unique_customers) from aggregate tables (double-counts across days).
- **Average basket**: always SUM(total_amount)/COUNT(invoice_no). Never AVG(avg_basket).
- **Forecasts**: only use the forecast_cache or ML.FORECAST SQL for forward-looking
  projections. Never use last-year-minus-X% arithmetic and call it a forecast.

{SCHEMA}
"""


def build_agent(instruction: str | None = None) -> Agent:
    """Build and return a new sql_agent instance with the given instruction.

    Args:
        instruction: System prompt override. Uses _BASE_PROMPT if None.

    Returns:
        A freshly constructed ADK Agent with the given instruction.
    """
    return Agent(
        name="evolvbi_sql_agent",
        model=_MODEL,
        description="Answers natural-language questions about mall performance using BigQuery SQL.",
        instruction=instruction or _BASE_PROMPT,
        tools=[query_warehouse, forecast_mall_revenue],
    )


# Default instance (used by app.py for quick CLI tests)
sql_agent = build_agent()
