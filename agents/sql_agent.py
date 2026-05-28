"""
sql_agent.py — EvolvBI SQL agent with Arize Phoenix tracing.

Answers natural-language questions from non-technical mall analysts
by generating and running BigQuery SQL, then explaining the results.
"""

import os

from phoenix.otel import register
from openinference.instrumentation.google_adk import GoogleADKInstrumentor

from google.adk.agents import Agent

from tools.bigquery_tools import query_warehouse, SCHEMA

_PHOENIX_ENDPOINT = os.environ.get(
    "PHOENIX_COLLECTOR_ENDPOINT",
    "https://app.phoenix.arize.com/v1/traces",
)

# PHOENIX_API_KEY env var is picked up automatically by register()
tracer_provider = register(
    project_name="evolvbi",
    endpoint=_PHOENIX_ENDPOINT,
)
GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)

_SYSTEM_PROMPT = f"""You are a retail analytics assistant for Istanbul mall managers.
You have access to a BigQuery warehouse via the query_warehouse tool.

When the user asks a business question:
1. Write a precise SQL query to answer it.
2. Run it with query_warehouse.
3. Explain the results in plain English — no jargon, no raw SQL in your reply.
4. Include specific numbers from the results to support your explanation.
5. If the question cannot be answered from the data, say so clearly.

Always use aggregate tables (agg_mall_daily, agg_tenant_daily) when possible.
Never guess — only state facts that appear in query results.

{SCHEMA}
"""

sql_agent = Agent(
    name="evolvbi_sql_agent",
    model="gemini-2.5-flash",
    description="Answers natural-language questions about mall performance using BigQuery SQL.",
    instruction=_SYSTEM_PROMPT,
    tools=[query_warehouse],
)
