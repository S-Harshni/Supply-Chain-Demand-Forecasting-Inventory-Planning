"""SQLite warehouse: load the clean tables and run the SQL analyses in sql/."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path("data/supply_chain.db")
SQL_DIR = Path("sql")

LINE_COLUMNS = [
    "order_id", "order_item_id", "order_date", "ship_date", "product_id", "category",
    "market", "region", "country", "segment", "shipping_mode", "order_status",
    "quantity", "unit_price", "discount", "net_sales", "profit", "unit_cost",
    "ship_days_scheduled", "ship_days_actual", "delay_days", "is_late",
    "shipment_cancelled", "is_demand", "after_catalogue_break",
]


def build(lines: pd.DataFrame, products: pd.DataFrame, path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    t = lines[LINE_COLUMNS].copy()
    for c in ("order_date", "ship_date"):
        t[c] = t[c].dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in ("shipment_cancelled", "is_demand", "after_catalogue_break"):
        t[c] = t[c].astype(int)
    t.to_sql("order_lines", con, index=False)
    products.to_sql("products", con, index=False)
    con.execute("CREATE INDEX idx_lines_product ON order_lines(product_id)")
    con.execute("CREATE INDEX idx_lines_date ON order_lines(order_date)")
    con.commit()
    return con


def run_queries(con: sqlite3.Connection, sql_dir: Path = SQL_DIR) -> dict[str, pd.DataFrame]:
    out = {}
    for f in sorted(sql_dir.glob("*.sql")):
        out[f.stem[3:]] = pd.read_sql_query(f.read_text(), con)
    return out
