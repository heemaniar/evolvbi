"""
tests/test_sql_agent.py — EvolvBI SQL agent structure tests.

Covers:
  1. build_agent() returns an Agent instance
  2. Agent has name="evolvbi_sql_agent"
  3. Agent instruction contains "goldengate_core" (not Istanbul)
  4. Agent instruction rendered with 2026 date anchor
  5. Agent has query_warehouse in its tools
  6. _BASE_PROMPT contains "Bay Area"

Heavy external dependencies (Phoenix, BigQuery, OpenInference) are patched
before import to prevent real network calls.

Run:  pytest -v tests/test_sql_agent.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path so 'agents' and 'tools' are importable
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True, scope="module")
def _patch_external():
    """Patch Phoenix, OpenInference and BigQuery before the module is imported."""
    patches = [
        patch("google.cloud.bigquery.Client"),
        patch("phoenix.otel.register", return_value=MagicMock()),
        patch(
            "openinference.instrumentation.google_adk.GoogleADKInstrumentor.instrument",
            return_value=None,
        ),
        # Also patch GoogleADKInstrumentor() constructor so instantiation works
        patch(
            "openinference.instrumentation.google_adk.GoogleADKInstrumentor.__init__",
            return_value=None,
        ),
    ]
    started = [p.start() for p in patches]
    yield started
    for p in patches:
        p.stop()


class TestBuildAgent:

    def test_build_agent_returns_agent_instance(self):
        from google.adk.agents import Agent
        from agents.sql_agent import build_agent
        agent = build_agent()
        assert isinstance(agent, Agent)

    def test_agent_name_is_evolvbi_sql_agent(self):
        from agents.sql_agent import build_agent
        agent = build_agent()
        assert agent.name == "evolvbi_sql_agent"

    def test_agent_instruction_contains_goldengate_core(self):
        """Instruction must reference goldengate_core, not Istanbul-era text."""
        from agents.sql_agent import build_agent
        agent = build_agent()
        assert "goldengate_core" in agent.instruction

    def test_agent_instruction_contains_2026_date(self):
        """The _TODAY anchor must render a 2026 date (current test year)."""
        from agents.sql_agent import build_agent
        agent = build_agent()
        assert "2026" in agent.instruction

    def test_agent_has_query_warehouse_tool(self):
        """Agent must include query_warehouse as a callable tool."""
        from agents.sql_agent import build_agent
        agent = build_agent()
        tool_names = []
        for t in agent.tools:
            if callable(t) and hasattr(t, "__name__"):
                tool_names.append(t.__name__)
        assert "query_warehouse" in tool_names

    def test_custom_instruction_is_used_when_provided(self):
        """When an instruction override is passed, it must be used."""
        from agents.sql_agent import build_agent
        custom = "You are a custom test agent."
        agent = build_agent(instruction=custom)
        assert agent.instruction == custom

    def test_default_instruction_used_when_none(self):
        """build_agent() with no args must use _BASE_PROMPT."""
        from agents.sql_agent import build_agent, _BASE_PROMPT
        agent = build_agent()
        assert agent.instruction == _BASE_PROMPT


class TestBasePrompt:

    def test_base_prompt_contains_bay_area(self):
        """_BASE_PROMPT must reference Bay Area malls, not Istanbul."""
        from agents.sql_agent import _BASE_PROMPT
        assert "Bay Area" in _BASE_PROMPT

    def test_base_prompt_contains_goldengate_core(self):
        from agents.sql_agent import _BASE_PROMPT
        assert "goldengate_core" in _BASE_PROMPT

    def test_base_prompt_does_not_contain_istanbul(self):
        from agents.sql_agent import _BASE_PROMPT
        assert "Istanbul" not in _BASE_PROMPT

    def test_base_prompt_contains_today_anchor(self):
        """The date anchor in _BASE_PROMPT must include the year 2026."""
        from agents.sql_agent import _BASE_PROMPT
        assert "2026" in _BASE_PROMPT
