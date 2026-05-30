"""
improver.py — EvolvBI improvement agent.

Reads failure traces from Arize Phoenix via the official Phoenix MCP server
(@arizeai/phoenix-mcp), groups them by failure pattern, and proposes specific
edits to the SQL agent's system prompt.

The improver agent has direct MCP access to Phoenix — it calls get-spans and
get-span-annotations itself rather than receiving pre-fetched context. This
means the agent decides what to query and how deep to look.

Run manually:
    python -c "from agents.improver import run_improvement_loop; import asyncio; asyncio.run(run_improvement_loop())"

Or triggered from Streamlit via the 'Run improvement loop' button.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StdioConnectionParams,
    StdioServerParameters,
)
from google.genai.types import Content, Part

# ── Phoenix MCP toolset ───────────────────────────────────────────────────────
# Uses the official @arizeai/phoenix-mcp npm package via npx.
# Gives the improver agent direct read access to Phoenix traces, spans,
# and eval annotations — no Python SDK intermediary.

def _phoenix_base_url() -> str:
    """Extract base URL from PHOENIX_COLLECTOR_ENDPOINT."""
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "https://app.phoenix.arize.com/v1/traces")
    # Strip path components to get the base URL
    # e.g. https://app.phoenix.arize.com/s/maniarheema/v1/traces → https://app.phoenix.arize.com
    from urllib.parse import urlparse
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


def _make_phoenix_mcp() -> McpToolset:
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=[
                    "-y",
                    "@arizeai/phoenix-mcp@latest",
                    "--baseUrl", _phoenix_base_url(),
                    "--apiKey", os.environ.get("PHOENIX_API_KEY", ""),
                ],
                env={**os.environ},
            ),
            timeout=45.0,
        ),
        # Read-only observability tools — no write/mutate operations
        tool_filter=[
            "list-projects",
            "get-project",
            "get-spans",
            "get-span-annotations",
        ],
    )


# ── Prompt loader (from BigQuery, persisted across Cloud Run instances) ────────
def _load_current_prompt_summary() -> str:
    """Read the live prompt from BigQuery prompt_store."""
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


# ── Improvement agent instruction ─────────────────────────────────────────────
_IMPROVER_INSTRUCTION = """You are an AI prompt engineer for the EvolvBI SQL analytics system.
You have direct access to the Arize Phoenix observability platform via MCP tools.

Your job when triggered:
1. Call `list-projects` to confirm the "evolvbi" project exists.
2. Call `get-spans` for the "evolvbi" project to retrieve recent root spans.
3. Call `get-span-annotations` with the span IDs to find spans labelled sql_success=fail
   or sql_relevance=fail.
4. If no failed spans exist, reply: "No failures found in Phoenix. All recent evals passed."
5. If failures exist, group them into 2-3 distinct failure PATTERNS
   (e.g. "wrong aggregation", "missing date filter", "hallucinated table name").
6. For each pattern, propose ONE specific, concrete edit to the SQL agent's system prompt.

Format your response as:

PATTERN 1: <short name>
Example span: <span ID>
Root cause: <one sentence>
Prompt edit: <exact text to add or change in the system prompt>

PATTERN 2: ...

Keep edits minimal and surgical — one sentence each. Do not rewrite the whole prompt.
Reference the current system prompt provided below when suggesting edits."""


_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
_SESSION_SVC = InMemorySessionService()


async def run_improvement_loop() -> str:
    """Connect to Phoenix via MCP, analyse failures, return proposed prompt edits."""
    phoenix_mcp = _make_phoenix_mcp()

    improver_agent = Agent(
        name="evolvbi_improver",
        model=_MODEL,
        description="Reads Phoenix failure traces via MCP and proposes SQL agent prompt improvements.",
        instruction=_IMPROVER_INSTRUCTION,
        tools=[phoenix_mcp],
    )

    message_text = (
        "Please connect to Phoenix, retrieve failure traces from the 'evolvbi' project, "
        "and propose prompt improvements based on the patterns you find.\n\n"
        f"{_load_current_prompt_summary()}"
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

    return "".join(reply_parts) or "No response from improvement agent."


if __name__ == "__main__":
    result = asyncio.run(run_improvement_loop())
    print(result)
