"""
bigquery_tools.py — Read-only BigQuery query tool for EvolvBI agents.

Reuses the same goldengate_core warehouse as GoldenGate Retail AI.

⚠️ All data is completely synthetic and generated for demonstration purposes.
"""

import re

from google.cloud import bigquery

PROJECT = "mallpulse-hackathon"
DATASET = "goldengate_core"
_client: bigquery.Client | None = None


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT)
    return _client


SCHEMA = """
BigQuery dataset: goldengate_core (project: mallpulse-hackathon)

⚠️ All data is completely synthetic and for demonstration purposes only.

Dimension tables:
  dim_mall       : mall_id, mall_name, city, state, country, tier,
                   gross_leasable_sqft, latitude, longitude, opened_year
  dim_tenant     : tenant_id, tenant_name, mall_id, category, subcategory,
                   unit_size_sqm, store_format,
                   effective_from (DATE), effective_to (DATE), is_replacement (BOOL)
                   NOTE: active tenants have effective_to >= CURRENT_DATE()
  dim_lease      : tenant_id, lease_start_date, lease_end_date,
                   monthly_base_rent (USD), rent_pct_of_sales
  dim_date       : date, day_of_week, is_weekend, is_holiday, holiday_name,
                   week_of_year, month, quarter, year
  dim_customer   : customer_id, gender, age_band, loyalty_tier

Fact tables:
  fact_transactions : invoice_no, tenant_id, mall_id, customer_id, date,
                      category, quantity, unit_price, total_amount (USD), payment_method
  fact_foot_traffic : mall_id, date, hour, estimated_visits
  fact_weather      : mall_id, date, temperature_c, precipitation_mm, weather_code

Aggregate tables (prefer these for speed):
  agg_tenant_daily : tenant_id, mall_id, date, transactions, revenue (USD),
                     avg_basket, unique_customers
  agg_mall_daily   : mall_id, date, total_revenue (USD), total_transactions,
                     unique_customers

Currency: All monetary values are in USD ($).
Date range: 2020-01-01 through yesterday (data updated daily via simulate_data.py)

Bay Area Malls (13 total):
  m01: Westfield Valley Fair (San Jose) — Premium Regional
  m02: Stanford Shopping Center (Palo Alto) — Luxury Open-Air
  m03: Santana Row (San Jose) — Lifestyle Premium
  m04: Westfield SF Centre (San Francisco) — CLOSED Aug 2023
  m05: Stonestown Galleria (San Francisco) — Community Regional
  m06: Bay Street Emeryville (Emeryville) — Lifestyle Open-Air
  m07: Great Mall (Milpitas) — Value Outlet
  m08: Hillsdale Shopping Center (San Mateo) — Mid-tier Regional
  m09: Stoneridge Shopping Center (Pleasanton) — Mid-tier Regional
  m10: Broadway Plaza (Walnut Creek) — Mid-tier Open-Air
  m11: Sunvalley Shopping Center (Concord) — Value Regional
  m12: Westfield Oakridge (San Jose) — Mid-tier Regional
  m13: San Francisco Premium Outlets (Livermore) — Premium Outlets

Always qualify table names as `mallpulse-hackathon.goldengate_core.<table_name>`.
"""


def query_warehouse(sql: str) -> str:
    """Execute a read-only SQL query against the GoldenGate Retail AI BigQuery warehouse.

    Args:
        sql: A valid BigQuery SELECT statement. Qualify tables as
             `mallpulse-hackathon.goldengate_core.<table_name>`.

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
