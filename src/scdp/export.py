"""Write results: JSON for the web dashboard, star-schema CSVs for Power BI."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS = Path("results")
DOCS = Path("docs")
POWERBI = Path("powerbi/data")


def _records(df: pd.DataFrame) -> list[dict]:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return json.loads(out.to_json(orient="records"))


def write_all(*, quality, sql, ts, scores, bt, policies, sim, risk, scenarios, drift,
              weekly, prods, fc_products, core, lines, champion, promise) -> dict:
    for p in (RESULTS, DOCS, POWERBI):
        p.mkdir(parents=True, exist_ok=True)

    names = prods.set_index("product_id")["product"]
    sc = scores.set_index("method")
    gb = sc.loc["Gradient boosting"]
    champ = sc.loc[champion]
    mode = sql["delivery_by_mode"]
    on_time = 100 - float((mode["late_pct"] * mode["shipments"]).sum() / mode["shipments"].sum())
    weekly_value = sim.groupby("week")["stock_value"].sum().iloc[4:]   # skip the 4-week warm-up from the opening stock
    status_counts = risk["status"].value_counts().to_dict()

    headline = {
        "order_lines": quality["rows_in_source"],
        "core_products": len(core),
        "core_share_of_units_pct": round(float(lines.loc[lines["is_demand"] & lines["product_id"].isin(core), "quantity"].sum() / lines.loc[lines["is_demand"], "quantity"].sum() * 100), 1),
        "weeks_of_history": len(weekly),
        "champion": champion,
        "champion_wape_4w_pct": float(champ["wape_4week_total_pct"]),
        "champion_bias_pct": float(champ["bias_pct"]),
        "gb_wape_4w_pct": float(gb["wape_4week_total_pct"]),
        "worst_wape_4w_pct": float(scores["wape_4week_total_pct"].max()),
        "worst_method": scores.iloc[-1]["method"],
        "on_time_delivery_pct": round(on_time, 1),
        "worst_mode": mode.iloc[0]["shipping_mode"],
        "worst_mode_late_pct": float(mode.iloc[0]["late_pct"]),
        "inventory_value_now": int(risk["stock_value_now"].sum()),
        "avg_inventory_value": int(round(weekly_value.mean())),
        "products_stockout_risk": int(status_counts.get("Stockout risk", 0)),
        "products_reorder_now": int(status_counts.get("Reorder now", 0)),
        "products_overstocked": int(status_counts.get("Overstocked", 0)),
        "reorder_value": int(risk["order_value"].sum()),
        "policies": policies,
        "promise": promise,
        "drift": drift,
        "growth_2016_vs_2015_pct": ts["growth_2016_vs_2015_pct"],
        "jan_sep_growth_2017_vs_2016_pct": ts["jan_sep_growth_2017_vs_2016_pct"],
        "peak_month": ts["peak_month"],
        "season_max": float(ts["season_index"].max()),
        "season_min": float(ts["season_index"].min()),
        "low_month": ts["low_month"],
    }

    # Total forecast vs actual across the backtest (all products).
    tot = bt.groupby(["method", "week"])[["forecast", "actual"]].sum().reset_index()
    fva = tot.pivot(index="week", columns="method", values="forecast")
    fva["Actual"] = tot.groupby("week")["actual"].first()

    dash = {
        "headline": headline,
        "quality": quality,
        "monthly_demand": _records(sql["monthly_demand"]),
        "season_index": {int(k): float(v) for k, v in ts["season_index"].items()},
        "department_growth": {k: float(v) for k, v in ts["department_growth_pct"].items()},
        "scores": _records(scores),
        "forecast_vs_actual": _records(fva.reset_index()),
        "delivery_by_mode": _records(mode),
        "delivery_by_market": _records(sql["delivery_by_market"]),
        "delivery_trend": _records(sql["delivery_trend"]),
        "category_summary": _records(sql["category_summary"].head(15)),
        "inventory_value_weekly": _records(weekly_value.rename("value").reset_index()),
        "risk": _records(risk[["product", "category", "status", "weekly_demand", "on_hand_now", "on_order_now",
                               "weeks_of_cover", "reorder_point", "recommended_order", "order_value",
                               "stock_value_now"]].round(1)),
        "scenarios": _records(scenarios),
    }
    (DOCS / "data.json").write_text(json.dumps(dash, indent=1, default=str))
    (RESULTS / "summary.json").write_text(json.dumps({"headline": headline, "quality": quality,
                                                       "scores": _records(scores),
                                                       "scenarios": _records(scenarios)}, indent=2, default=str))

    # ---------- Power BI star schema ----------
    dim_product = prods.copy()
    dim_product["core_product"] = dim_product["product_id"].isin(core)
    dim_product.to_csv(POWERBI / "dim_product.csv", index=False)

    wk = lines[lines["is_demand"]].copy()
    wk["week"] = (wk["order_date"].dt.normalize() - pd.to_timedelta(wk["order_date"].dt.weekday, unit="D"))
    fact_demand = (wk.groupby(["week", "product_id", "market"])
                     .agg(units=("quantity", "sum"), net_sales=("net_sales", "sum"), profit=("profit", "sum"),
                          orders=("order_id", "nunique"))
                     .reset_index())
    fact_demand.to_csv(POWERBI / "fact_weekly_demand.csv", index=False, float_format="%.2f")

    dl = lines[~lines["shipment_cancelled"]].copy()
    dl["month"] = dl["order_date"].dt.to_period("M").dt.to_timestamp()
    fact_delivery = (dl.groupby(["month", "market", "shipping_mode", "category"])
                       .agg(shipments=("order_item_id", "count"), late_shipments=("is_late", "sum"),
                            promised_days=("ship_days_scheduled", "sum"), actual_days=("ship_days_actual", "sum"))
                       .reset_index())
    fact_delivery.to_csv(POWERBI / "fact_delivery.csv", index=False)

    bt.to_csv(POWERBI / "fact_forecast_backtest.csv", index=False, float_format="%.2f")
    sim.to_csv(POWERBI / "fact_inventory_simulation.csv", index=False, float_format="%.2f")
    risk.to_csv(POWERBI / "reorder_recommendations.csv", index=False, float_format="%.2f")
    scenarios.to_csv(POWERBI / "scenarios.csv", index=False)
    scores.to_csv(POWERBI / "forecast_accuracy.csv", index=False)

    return {"headline": headline}
