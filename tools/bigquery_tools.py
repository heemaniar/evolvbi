"""
bigquery_tools.py — Read-only BigQuery query tool for EvolvBI agents.

Reuses the same mallpulse_core warehouse as MallPulse.
"""

import re

from google.cloud import bigquery

PROJECT = "mallpulse-hackathon"
DATASET = "mallpulse_core"
_client: bigquery.Client | None = None


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT)
    return _client


SCHEMA = """
BigQuery dataset: mallpulse_core (project: mallpulse-hackathon)

Dimension tables:
  dim_mall       : mall_id, mall_name, city, country, latitude, longitude,
                   gross_leasable_sqm, opened_year
  dim_tenant     : tenant_id, tenant_name, mall_id, category, subcategory,
                   unit_size_sqm, store_format,
                   effective_from (DATE), effective_to (DATE), is_replacement (BOOL)
  dim_lease      : tenant_id, lease_start_date, lease_end_date,
                   monthly_base_rent, rent_pct_of_sales
  dim_date       : date, day_of_week, is_weekend, is_holiday, holiday_name,
                   week_of_year, month, quarter, year
  dim_customer   : customer_id, gender, age_band, loyalty_tier

Fact tables:
  fact_transactions : invoice_no, tenant_id, mall_id, customer_id, date,
                      category, quantity, unit_price, total_amount, payment_method
  fact_foot_traffic : mall_id, date, hour, estimated_visits
  fact_weather      : mall_id, date, temperature_c, precipitation_mm, weather_code

Aggregate tables (prefer these for speed):
  agg_tenant_daily : tenant_id, mall_id, date, transactions, revenue,
                     avg_basket, unique_customers
  agg_mall_daily   : mall_id, date, total_revenue, total_transactions,
                     unique_customers

Date range: 2021-01-01 through yesterday (data updated daily via simulate_data.py)
Malls: Kanyon, Forum Istanbul, Metrocity, Metropol AVM, Istinye Park,
       Mall of Istanbul, Emaar Square Mall, Cevahir AVM, Viaport Outlet,
       Zorlu Center
"""


def query_warehouse(sql: str) -> str:
    """Execute a read-only SQL query against the MallPulse BigQuery warehouse.

    Args:
        sql: A valid BigQuery SELECT statement. Qualify tables as
             `mallpulse-hackathon.mallpulse_core.<table_name>`.

    Returns:
        Query results as a markdown table (up to 50 rows), or an error message.
    """
    normalised = sql.strip().upper()
    for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "TRUNCATE", "MERGE"):
        if re.search(rf"\b{keyword}\b", normalised):
            return f"Error: {keyword} statements are not allowed. Use SELECT only."

    try:
        client = _get_client()
        job = client.query(sql)
        iterator = job.result(max_results=50)
        rows = list(iterator)

        if not rows:
            return "Query returned no rows."

        headers = [f.name for f in iterator.schema]
        md = "| " + " | ".join(headers) + " |\n"
        md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
        for row in rows:
            md += "| " + " | ".join(str(v) if v is not None else "" for v in row.values()) + " |\n"
        return md

    except Exception as e:
        return f"BigQuery error: {e}"
