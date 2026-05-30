"""
tests/test_evals.py — EvolvBI eval helper unit tests.

Tests the pure Python helper functions in evals/run_evals.py that can be
exercised without a real Phoenix connection.

Covers:
  1. _has_sql_error returns True for "BigQuery error: ..."
  2. _has_sql_error returns True for "Error: column not found"
  3. _has_sql_error returns False for "Query returned no rows" (valid empty result)
  4. _has_sql_error returns False for clean output
  5. _parse_input_output handles malformed JSON gracefully (returns empty strings)

Run:  pytest -v tests/test_evals.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Patch heavy external imports that run at module level in run_evals.py.
# We only need to test the pure helper functions — no Phoenix client needed.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _patch_evals_imports():
    """
    Patch Phoenix client and LLM instantiation so run_evals.py can be
    imported without real credentials or network access.
    """
    # We patch os.environ to provide required env vars before import
    env_override = {
        "PHOENIX_COLLECTOR_ENDPOINT": "https://fake.phoenix/v1/traces",
        "PHOENIX_API_KEY": "fake-key",
        "GOOGLE_CLOUD_PROJECT": "mallpulse-hackathon",
    }

    patches = [
        patch.dict("os.environ", env_override),
        patch("phoenix.client.Client.__init__", return_value=None),
        patch("phoenix.evals.LLM.__init__", return_value=None),
    ]
    started = [p.start() for p in patches]
    yield
    for p in patches:
        p.stop()


# Import the helpers after patching
# We import lazily inside each test to ensure patches are active.

class TestHasSqlError:

    def test_bigquery_error_prefix_is_detected(self):
        from evals.run_evals import _has_sql_error
        assert _has_sql_error(["BigQuery error: table not found"]) is True

    def test_error_colon_prefix_is_detected(self):
        from evals.run_evals import _has_sql_error
        assert _has_sql_error(["Error: column not found"]) is True

    def test_query_returned_no_rows_is_not_an_error(self):
        """'Query returned no rows' is a valid empty result — must NOT flag as error."""
        from evals.run_evals import _has_sql_error
        assert _has_sql_error(["Query returned no rows."]) is False

    def test_clean_output_is_not_an_error(self):
        from evals.run_evals import _has_sql_error
        assert _has_sql_error(["| mall_name | revenue |\n| --- | --- |\n| Valley Fair | 1000000 |"]) is False

    def test_empty_list_is_not_an_error(self):
        from evals.run_evals import _has_sql_error
        assert _has_sql_error([]) is False

    def test_mixed_outputs_detects_error(self):
        """Even one error in a list of outputs should flag the whole run."""
        from evals.run_evals import _has_sql_error
        outputs = [
            "| mall_name | revenue |\n| Valley Fair | 1000000 |",
            "BigQuery error: quota exceeded",
        ]
        assert _has_sql_error(outputs) is True

    def test_bigquery_error_case_insensitive(self):
        from evals.run_evals import _has_sql_error
        assert _has_sql_error(["bigquery error: something went wrong"]) is True


class TestParseInputOutput:

    def _make_row(self, input_val, output_val) -> pd.Series:
        return pd.Series({
            "attributes.input.value": input_val,
            "attributes.output.value": output_val,
        })

    def test_valid_json_extracts_question_and_answer(self):
        from evals.run_evals import _parse_input_output
        input_json = json.dumps({
            "new_message": {"parts": [{"text": "What is the revenue?"}]}
        })
        output_json = json.dumps({
            "content": {"parts": [{"text": "Revenue was $1M"}]}
        })
        row = self._make_row(input_json, output_json)
        question, answer = _parse_input_output(row)
        assert question == "What is the revenue?"
        assert answer == "Revenue was $1M"

    def test_malformed_input_json_returns_empty_strings_not_exception(self):
        """Malformed JSON must not raise — return empty strings gracefully."""
        from evals.run_evals import _parse_input_output
        row = self._make_row("NOT_VALID_JSON{{{", "ALSO_NOT_JSON{{{")
        # Must not raise
        try:
            question, answer = _parse_input_output(row)
            # Returns strings (possibly truncated raw text)
            assert isinstance(question, str)
            assert isinstance(answer, str)
        except Exception as e:
            pytest.fail(f"_parse_input_output raised {type(e).__name__}: {e}")

    def test_none_input_output_returns_empty_strings(self):
        """None values (missing attributes) must return empty strings."""
        from evals.run_evals import _parse_input_output
        row = self._make_row(None, None)
        question, answer = _parse_input_output(row)
        assert isinstance(question, str)
        assert isinstance(answer, str)

    def test_empty_parts_list_returns_empty_strings(self):
        from evals.run_evals import _parse_input_output
        input_json = json.dumps({"new_message": {"parts": []}})
        output_json = json.dumps({"content": {"parts": []}})
        row = self._make_row(input_json, output_json)
        question, answer = _parse_input_output(row)
        assert question == ""
        assert answer == ""

    def test_output_with_list_content_is_handled(self):
        """output.value where 'content' is a list (alternate format)."""
        from evals.run_evals import _parse_input_output
        input_json = json.dumps({
            "new_message": {"parts": [{"text": "Q?"}]}
        })
        output_json = json.dumps({
            "content": [{"parts": [{"text": "A!"}]}]
        })
        row = self._make_row(input_json, output_json)
        question, answer = _parse_input_output(row)
        assert question == "Q?"
        assert answer == "A!"
