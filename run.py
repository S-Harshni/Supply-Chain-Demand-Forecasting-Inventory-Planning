"""Run the whole pipeline: clean -> SQL -> demand analysis -> forecast -> inventory -> scenarios -> exports."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))
from scdp import data, demand, inventory, warehouse, export  # noqa: E402


def main() -> None:
    raw = data.load_raw(data.download())
    lines, quality = data.clean(raw)
    prods = data.products(lines)
    print("Clean:", quality)

    con = warehouse.build(lines, prods)
    sql = warehouse.run_queries(con)
    con.close()

    weekly_p = demand.weekly_matrix(lines)
    ts = demand.trend_and_seasonality(weekly_p, prods)
    churn = demand.assortment_changes(weekly_p)
    quality.update(churn)
    core = demand.forecastable(weekly_p)
    weekly = weekly_p
    fc_products = core
    print(f"{len(weekly)} complete weeks, {len(core)} core products forecast", churn)

    bt, scores = demand.backtest(weekly, fc_products)
    print(scores.to_string(index=False))
    champion = scores.iloc[0]["method"]   # lowest 4-week error in the backtest
    print("Champion:", champion)

    # Planning inputs for the stock simulation over the backtest weeks.
    test_weeks = sorted(bt["week"].unique())
    actual = weekly.loc[test_weeks, fc_products]
    first_origin = bt["origin"].min()
    hist = weekly.loc[:first_origin, fc_products]
    sigma = hist.iloc[-26:].std()
    pinfo = prods.set_index("product_id")
    info = pinfo.loc[fc_products, ["product", "category", "unit_cost"]]
    unit_cost = info["unit_cost"]

    def plan(method: str) -> pd.DataFrame:
        b = bt[bt["method"] == method]
        rate = b.groupby(["origin", "product_id"])["forecast"].transform("mean")
        return b.assign(rate=rate).pivot_table(index="week", columns="product_id", values="rate")

    policies = {}
    sims = {}
    for method in dict.fromkeys([champion, "Gradient boosting", "4-week average"]):
        sim, summ = inventory.simulate(actual, plan(method), sigma, unit_cost)
        sims[method] = (sim, summ)
        policies[method] = {
            "fill_rate_pct": round(float(sim["served"].sum() / sim["demand"].sum() * 100), 2),
            "lost_units": int(round(sim["lost_sales"].sum())),
            "stockout_product_weeks": int((sim["lost_sales"] > 0).sum()),
            "avg_inventory_value": int(round(sim.groupby("week")["stock_value"].sum().mean())),
        }
    print("Policies:", policies)

    sim, summ = sims[champion]
    nxt = demand.forecast_next_with(champion, weekly, fc_products)
    next_mu = nxt.groupby("product_id")["forecast"].mean()
    recent_sigma = weekly[fc_products].iloc[-26:].std()
    risk = inventory.risk_table(summ, next_mu, recent_sigma, unit_cost, info.drop(columns="unit_cost"))
    print(risk["status"].value_counts().to_dict())

    scenarios = inventory.run_scenarios(actual, plan(champion), sigma, unit_cost)
    print(scenarios.to_string(index=False))

    # Root cause of late deliveries: are the promises realistic?
    ship = lines[~lines["shipment_cancelled"]]
    typical = ship.groupby("shipping_mode")["ship_days_actual"].median()
    realistic = np.maximum(ship["ship_days_scheduled"], ship["shipping_mode"].map(typical))
    promise = {
        "late_pct_now": round(float(ship["is_late"].mean() * 100), 1),
        "late_pct_with_realistic_promise": round(float((ship["ship_days_actual"] > realistic).mean() * 100), 1),
        "typical_days_by_mode": {k: float(v) for k, v in typical.items()},
        "promised_days_by_mode": {k: float(v) for k, v in ship.groupby("shipping_mode")["ship_days_scheduled"].median().items()},
    }
    print("Promise:", promise)

    # What happened after the planning date: the catalogue change.
    after = lines[lines["is_demand"] & lines["after_catalogue_break"]].copy()
    after["week"] = demand.week_start(after["order_date"])
    after = after[after["product_id"].isin(core)]
    first4 = sorted(after["week"].unique())[:4]
    actual_after = after[after["week"].isin(first4)]["quantity"].sum()
    drift = {
        "forecast_units_next_4_weeks": int(round(nxt["forecast"].sum())),
        "actual_units_next_4_weeks": int(actual_after),
        "actual_vs_forecast_pct": round(float(actual_after / nxt["forecast"].sum() * 100), 1),
    }
    print("Drift:", drift)

    results = export.write_all(
        quality=quality, sql=sql, ts=ts, scores=scores, bt=bt, policies=policies, sim=sim,
        risk=risk, scenarios=scenarios, drift=drift, weekly=weekly, prods=prods,
        fc_products=fc_products, core=core, lines=lines, champion=champion, promise=promise,
    )
    print(json.dumps(results["headline"], indent=2))


if __name__ == "__main__":
    main()
