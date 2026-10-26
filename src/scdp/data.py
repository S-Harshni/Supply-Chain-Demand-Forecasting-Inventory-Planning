"""Load the DataCo order lines and turn them into a clean, documented table.

Every cleaning decision is counted in a quality report so it can be checked
(and shown on the dashboard) instead of happening silently.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

RAW_URL = (
    "https://raw.githubusercontent.com/McGill-MMA-EnterpriseAnalytics/"
    "DataCo_Supply_Chain/main/data/raw/DataCoSupplyChainDataset.csv"
)
RAW_PATH = Path("data/raw/DataCoSupplyChainDataset.csv")

# Personal or empty columns that the analysis never needs.
DROP_COLUMNS = [
    "Customer Email", "Customer Password", "Customer Fname", "Customer Lname",
    "Customer Street", "Customer Zipcode", "Order Zipcode", "Product Description",
    "Product Image", "Latitude", "Longitude",
]

RENAME = {
    "Type": "payment_type",
    "Days for shipping (real)": "ship_days_actual",
    "Days for shipment (scheduled)": "ship_days_scheduled",
    "Delivery Status": "delivery_status",
    "Late_delivery_risk": "late_flag_source",
    "Category Name": "category",
    "Department Name": "department",
    "Customer Segment": "segment",
    "Market": "market",
    "Order Region": "region",
    "Order Country": "country",
    "order date (DateOrders)": "order_date",
    "shipping date (DateOrders)": "ship_date",
    "Order Id": "order_id",
    "Order Item Id": "order_item_id",
    "Order Item Quantity": "quantity",
    "Order Item Product Price": "unit_price",
    "Order Item Discount": "discount",
    "Order Item Total": "net_sales",
    "Order Profit Per Order": "profit",
    "Order Status": "order_status",
    "Product Card Id": "product_id",
    "Product Name": "product",
    "Shipping Mode": "shipping_mode",
}

# Orders that never became real demand.
NOT_DEMAND = {"CANCELED", "SUSPECTED_FRAUD"}

# From October 2017 the catalogue changes: most products stop selling and
# volume falls by about 80%. Forecasting across that break would be wrong.
CATALOGUE_BREAK = pd.Timestamp("2017-10-01")


def download(path: Path = RAW_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        print(f"Downloading DataCo data to {path} (about 96 MB)...")
        urllib.request.urlretrieve(RAW_URL, path)
    return path


def load_raw(path: Path = RAW_PATH) -> pd.DataFrame:
    return pd.read_csv(path, encoding="latin-1")


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    q: dict = {"rows_in_source": int(len(raw))}
    df = raw.drop(columns=[c for c in DROP_COLUMNS if c in raw.columns])
    q["personal_or_empty_columns_removed"] = int(len(raw.columns) - len(df.columns))
    df = df[[c for c in RENAME if c in df.columns]].rename(columns=RENAME)

    q["exact_duplicate_rows"] = int(df.duplicated().sum())
    df = df.drop_duplicates()

    names = df["product"].astype(str)
    q["product_names_with_stray_spaces"] = int((names != names.str.strip()).groupby(df["product_id"]).any().sum())
    df["product"] = names.str.strip()

    df["order_date"] = pd.to_datetime(df["order_date"], format="%m/%d/%Y %H:%M")
    df["ship_date"] = pd.to_datetime(df["ship_date"], format="%m/%d/%Y %H:%M")
    df["order_status"] = df["order_status"].str.upper().str.strip()

    df["is_demand"] = ~df["order_status"].isin(NOT_DEMAND)
    q["cancelled_lines"] = int((df["order_status"] == "CANCELED").sum())
    q["suspected_fraud_lines"] = int((df["order_status"] == "SUSPECTED_FRAUD").sum())

    # Delivery: a line is late when it took longer than promised.
    df["delay_days"] = df["ship_days_actual"] - df["ship_days_scheduled"]
    df["is_late"] = (df["delay_days"] > 0).astype(int)
    df["shipment_cancelled"] = df["delivery_status"].eq("Shipping canceled")
    mismatch = (df["is_late"] != df["late_flag_source"]) & ~df["shipment_cancelled"]
    q["late_flag_mismatches_excluding_cancelled"] = int(mismatch.sum())
    q["cancelled_shipments_excluded_from_delivery_kpis"] = int(df["shipment_cancelled"].sum())

    # Unit cost per product: sales minus profit gives a noisy per-line cost, so
    # take the product median and never let it exceed the selling price.
    line_cost = (df["net_sales"] - df["profit"]) / df["quantity"]
    cost = line_cost.groupby(df["product_id"]).median()
    price = df.groupby("product_id")["unit_price"].median()
    cost = np.minimum(cost, price)
    df["unit_cost"] = df["product_id"].map(cost).round(2)
    q["products_with_cost_capped_at_price"] = int((line_cost.groupby(df["product_id"]).median() > price).sum())

    df["after_catalogue_break"] = df["order_date"] >= CATALOGUE_BREAK
    q["lines_after_catalogue_break"] = int(df["after_catalogue_break"].sum())
    pre = set(df.loc[~df["after_catalogue_break"], "product_id"])
    post = set(df.loc[df["after_catalogue_break"], "product_id"])
    q["products_before_break"] = len(pre)
    q["products_after_break"] = len(post)
    q["products_that_stop_at_break"] = len(pre - post)

    q["rows_clean"] = int(len(df))
    q["demand_lines"] = int(df["is_demand"].sum())
    q["first_order"] = str(df["order_date"].min().date())
    q["last_order"] = str(df["order_date"].max().date())
    return df.reset_index(drop=True), q


def products(lines: pd.DataFrame) -> pd.DataFrame:
    g = lines.groupby("product_id")
    out = pd.DataFrame({
        "product": g["product"].first(),
        "category": g["category"].first(),
        "department": g["department"].first(),
        "unit_price": g["unit_price"].median().round(2),
        "unit_cost": g["unit_cost"].first(),
    })
    return out.reset_index()
