import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from scdp import data, demand, inventory, warehouse  # noqa: E402


def fake_raw(weeks: int = 80, seed: int = 0) -> pd.DataFrame:
    """A small DataCo-shaped table: 3 products, daily orders, one cancelled and one fraud line."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2016-01-04")
    rows = []
    oid = 0
    for day in range(weeks * 7):
        date = start + pd.Timedelta(days=day)
        for pid, name, price in [(1, "Alpha ", 50.0), (2, "Beta", 20.0), (3, "Gamma", 10.0)]:
            qty = int(rng.integers(1, 5))
            oid += 1
            sched = int(rng.choice([1, 2, 4]))
            actual = sched + int(rng.choice([0, 0, 1]))
            rows.append({
                "Type": "DEBIT", "Days for shipping (real)": actual, "Days for shipment (scheduled)": sched,
                "Delivery Status": "Late delivery" if actual > sched else "Shipping on time",
                "Late_delivery_risk": int(actual > sched), "Category Name": "Cat", "Department Name": "Dept",
                "Customer Segment": "Consumer", "Market": "Europe", "Order Region": "West", "Order Country": "X",
                "order date (DateOrders)": date.strftime("%m/%d/%Y %H:%M"),
                "shipping date (DateOrders)": (date + pd.Timedelta(days=actual)).strftime("%m/%d/%Y %H:%M"),
                "Order Id": oid, "Order Item Id": oid, "Order Item Quantity": qty, "Order Item Product Price": price,
                "Order Item Discount": 0.0, "Order Item Total": qty * price, "Order Profit Per Order": qty * price * 0.2,
                "Order Status": "COMPLETE", "Product Card Id": pid, "Product Name": name,
                "Shipping Mode": {1: "First Class", 2: "Second Class", 4: "Standard Class"}[sched],
                "Customer Email": "XXX", "Customer Password": "XXX",
            })
    df = pd.DataFrame(rows)
    df.loc[0, "Order Status"] = "CANCELED"
    df.loc[1, "Order Status"] = "SUSPECTED_FRAUD"
    return df


@pytest.fixture(scope="module")
def clean():
    return data.clean(fake_raw())


def test_cleaning_removes_personal_data_and_counts_everything(clean):
    lines, q = clean
    assert "Customer Email" not in lines.columns and "Customer Password" not in lines.columns
    assert q["cancelled_lines"] == 1 and q["suspected_fraud_lines"] == 1
    assert lines["is_demand"].sum() == len(lines) - 2
    assert q["product_names_with_stray_spaces"] == 1
    assert (lines["product"] == lines["product"].str.strip()).all()


def test_late_flag_matches_dates(clean):
    lines, q = clean
    assert ((lines["ship_days_actual"] > lines["ship_days_scheduled"]).astype(int) == lines["is_late"]).all()
    assert q["late_flag_mismatches_excluding_cancelled"] == 0


def test_unit_cost_never_above_price(clean):
    lines, _ = clean
    assert (lines["unit_cost"] <= lines["unit_price"] + 1e-9).all()


def test_weekly_matrix_has_only_complete_weeks(clean):
    lines, _ = clean
    w = demand.weekly_matrix(lines)
    assert (w.index.weekday == 0).all()
    assert w.index.is_monotonic_increasing and w.index.to_series().diff().dropna().eq(pd.Timedelta(days=7)).all()
    total_in_weeks = w.to_numpy().sum()
    d = lines[lines["is_demand"]]
    assert total_in_weeks <= d["quantity"].sum()


def test_features_use_no_future_data(clean):
    lines, _ = clean
    w = demand.weekly_matrix(lines)
    prods = list(w.columns)
    origin = 60
    full = demand._features(w, prods, origin, 2)
    truncated = w.copy()
    truncated.iloc[origin + 1:] = 999.0          # change everything after the origin
    cut = demand._features(truncated, prods, origin, 2).drop(columns=["same_week_last_year"])
    pd.testing.assert_frame_equal(full.drop(columns=["same_week_last_year"]), cut)


def test_wape():
    assert demand.wape([10, 10], [12, 8]) == pytest.approx(0.2)


def test_reorder_point_formula():
    mu, sd, cost = pd.Series([100.0]), pd.Series([10.0]), pd.Series([5.0])
    p = inventory.reorder_parameters(mu, sd, cost, inventory.Policy(lead_time_weeks=2, review_weeks=1))
    expected = 100 * 3 + 1.6449 * 10 * np.sqrt(3)
    assert p["reorder_point"].iloc[0] == pytest.approx(expected, abs=0.1)


def test_simulation_conserves_units():
    weeks = pd.date_range("2017-01-02", periods=20, freq="7D")
    demand_df = pd.DataFrame({"a": np.full(20, 50.0), "b": np.r_[np.full(10, 20.0), np.full(10, 60.0)]}, index=weeks)
    plan = pd.DataFrame({"a": 50.0, "b": 20.0}, index=weeks)
    sim, summary = inventory.simulate(demand_df, plan, pd.Series({"a": 5.0, "b": 5.0}), pd.Series({"a": 1.0, "b": 1.0}))
    assert np.allclose(sim["served"] + sim["lost_sales"], sim["demand"])
    assert (sim["on_hand_end"] >= -1e-9).all()
    # demand triples for b without the plan changing, so b must run short
    assert summary.loc["b", "stockout_weeks"] > 0 and summary.loc["a", "stockout_weeks"] == 0


def test_longer_lead_time_lowers_fill_rate():
    rng = np.random.default_rng(1)
    weeks = pd.date_range("2017-01-02", periods=30, freq="7D")
    d = pd.DataFrame({"a": rng.normal(100, 15, 30).clip(0)}, index=weeks)
    plan = pd.DataFrame({"a": 100.0}, index=weeks)
    s, c = pd.Series({"a": 15.0}), pd.Series({"a": 1.0})
    base, _ = inventory.simulate(d, plan, s, c, inventory.Policy())
    late, _ = inventory.simulate(d, plan, s, c, inventory.Policy(actual_lead_time_weeks=4))
    assert late["served"].sum() < base["served"].sum()


def test_sql_queries_run(clean, tmp_path):
    lines, _ = clean
    con = warehouse.build(lines, data.products(lines), tmp_path / "t.db")
    out = warehouse.run_queries(con, ROOT / "sql")
    assert set(out) >= {"monthly_demand", "delivery_by_mode", "category_summary"}
    assert out["monthly_demand"]["units"].sum() == lines.loc[lines["is_demand"], "quantity"].sum()
