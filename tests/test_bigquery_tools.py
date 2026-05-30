"""
tests/test_bigquery_tools.py — EvolvBI

Covers:
  1. query_warehouse happy path returns markdown
  2. query_warehouse blocks DML statements
  3. query_warehouse empty result returns "no rows" string
  4. query_warehouse BigQuery error returns string, not exception
  5. SCHEMA constant references goldengate_core (not old mallpulse_core)
  6. SCHEMA constant lists Bay Area mall names

Run:  pytest -v tests/test_bigquery_tools.py
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_iterator(values_list: list[list], schema_names: list[str]) -> MagicMock:
    """Build a mock RowIterator that supports iteration and .schema."""
    rows = []
    for vals in values_list:
        row = MagicMock()
        row.values.return_value = vals  # evolvbi iterates row.values()
        rows.append(row)

    schema_fields = []
    for n in schema_names:
        f = MagicMock()
        f.name = n
        schema_fields.append(f)

    iterator = MagicMock()
    iterator.__iter__ = MagicMock(return_value=iter(rows))
    type(iterator).schema = PropertyMock(return_value=schema_fields)
    return iterator


def _patch_bq(values_list: list[list], schema_names: list[str]):
    mock_iter = _make_iterator(values_list, schema_names)
    mock_job = MagicMock()
    mock_job.result.return_value = mock_iter
    mock_client = MagicMock()
    mock_client.query.return_value = mock_job
    return patch("tools.bigquery_tools._get_client", return_value=mock_client)


def _patch_bq_error(exc: Exception):
    mock_client = MagicMock()
    mock_client.query.side_effect = exc
    return patch("tools.bigquery_tools._get_client", return_value=mock_client)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestQueryWarehouse:

    def test_happy_path_returns_markdown(self):
        """A successful SELECT returns a markdown table."""
        with _patch_bq([["Valley Fair", 1_200_000]], ["mall_name", "revenue_usd"]):
            from tools.bigquery_tools import query_warehouse
            result = query_warehouse("SELECT mall_name, revenue_usd FROM agg_mall_daily")
        assert "| mall_name | revenue_usd |" in result
        assert "Valley Fair" in result

    def test_blocks_insert(self):
        from tools.bigquery_tools import query_warehouse
        result = query_warehouse("INSERT INTO dim_mall VALUES (1)")
        assert "Error" in result
        assert "INSERT" in result

    def test_blocks_update(self):
        from tools.bigquery_tools import query_warehouse
        result = query_warehouse("UPDATE dim_mall SET city = 'X'")
        assert "Error" in result

    def test_blocks_delete(self):
        from tools.bigquery_tools import query_warehouse
        result = query_warehouse("DELETE FROM dim_tenant WHERE 1=1")
        assert "Error" in result

    def test_blocks_drop(self):
        from tools.bigquery_tools import query_warehouse
        result = query_warehouse("DROP TABLE goldengate_core.dim_tenant")
        assert "Error" in result

    def test_blocks_truncate(self):
        from tools.bigquery_tools import query_warehouse
        result = query_warehouse("TRUNCATE TABLE fact_transactions")
        assert "Error" in result

    def test_blocks_merge(self):
        from tools.bigquery_tools import query_warehouse
        result = query_warehouse("MERGE target USING source ON (1=1)")
        assert "Error" in result

    def test_empty_result_returns_no_rows_string(self):
        with _patch_bq([], ["mall_name"]):
            from tools.bigquery_tools import query_warehouse
            result = query_warehouse("SELECT mall_name FROM dim_mall WHERE 1=0")
        assert "no rows" in result.lower()

    def test_bigquery_error_returns_string_not_exception(self):
        """BQ errors must be caught; tools must never raise."""
        with _patch_bq_error(Exception("BQ service unavailable")):
            from tools.bigquery_tools import query_warehouse
            result = query_warehouse("SELECT 1")
        assert isinstance(result, str)
        assert "error" in result.lower()


class TestSchema:

    def test_schema_references_goldengate_core(self):
        """SCHEMA must reference 'goldengate_core', not the old 'mallpulse_core'."""
        from tools.bigquery_tools import SCHEMA
        assert "goldengate_core" in SCHEMA

    def test_schema_does_not_reference_mallpulse_core(self):
        """Old dataset name must not appear in SCHEMA."""
        from tools.bigquery_tools import SCHEMA
        assert "mallpulse_core" not in SCHEMA

    def test_schema_mentions_valley_fair(self):
        from tools.bigquery_tools import SCHEMA
        assert "Valley Fair" in SCHEMA

    def test_schema_mentions_stanford_shopping_center(self):
        from tools.bigquery_tools import SCHEMA
        assert "Stanford" in SCHEMA

    def test_schema_mentions_santana_row(self):
        from tools.bigquery_tools import SCHEMA
        assert "Santana Row" in SCHEMA
