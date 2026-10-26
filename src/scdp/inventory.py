"""Reorder points, a weekly stock simulation on real demand, risk flags and scenarios.

DataCo has no stock records, so stock is simulated: each product starts with
a normal working stock and follows a reorder-point policy while the real
weekly demand of the backtest period is drawn down. Lead time, service level
and costs are planning assumptions, set in one place below.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

Z = {0.90: 1.2816, 0.95: 1.6449, 0.98: 2.0537}


@dataclass(frozen=True)
class Policy:
    lead_time_weeks: int = 2          # supplier lead time assumed when planning
    actual_lead_time_weeks: int = 2   # lead time that really happens (scenarios change this)
    review_weeks: int = 1             # stock is reviewed and ordered once a week
    service_level: float = 0.95
    order_cost: float = 50.0          # cost per purchase order
    holding_rate: float = 0.25        # yearly holding cost as a share of unit cost
    demand_multiplier: float = 1.0    # demand shock applied to real demand (scenarios)


def reorder_parameters(mu: pd.Series, sigma: pd.Series, unit_cost: pd.Series, pol: Policy) -> pd.DataFrame:
    """Safety stock, reorder point and economic order quantity per product (weekly units)."""
    z = Z[pol.service_level]
    cover = pol.lead_time_weeks + pol.review_weeks   # stock must last until the next order arrives
    safety = z * sigma * np.sqrt(cover)
    rop = mu * cover + safety
    annual = mu * 52
    hold = pol.holding_rate * unit_cost.clip(lower=0.01)
    eoq = np.sqrt(2 * annual * pol.order_cost / hold)
    eoq = np.maximum(eoq, mu)  # at least one week of demand per order
    return pd.DataFrame({"weekly_demand": mu, "demand_sd": sigma, "safety_stock": safety,
                         "reorder_point": rop, "order_qty": eoq}).round(1)


def simulate(demand: pd.DataFrame, plan_mu: pd.DataFrame, sigma: pd.Series,
             unit_cost: pd.Series, pol: Policy = Policy()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Week-by-week stock for each product.

    demand:   real weekly units (rows = weeks, columns = products)
    plan_mu:  the forecast of weekly demand in force each week (same shape)
    Returns the weekly stock table and a per-product summary.
    """
    weeks, prods = demand.index, demand.columns
    D = demand.to_numpy() * pol.demand_multiplier
    MU = plan_mu.reindex(index=weeks, columns=prods).to_numpy()
    sig = sigma.reindex(prods).to_numpy()
    cost = unit_cost.reindex(prods).to_numpy()
    n_w, n_p = D.shape

    first = reorder_parameters(pd.Series(MU[0], index=prods), sigma.reindex(prods), unit_cost.reindex(prods), pol)
    on_hand = (first["reorder_point"] + first["order_qty"] / 2).to_numpy()
    pipeline = np.zeros((n_w + pol.actual_lead_time_weeks + 1, n_p))
    rows = []
    for t in range(n_w):
        on_hand = on_hand + pipeline[t]
        params = reorder_parameters(pd.Series(MU[t], index=prods), sigma.reindex(prods), unit_cost.reindex(prods), pol)
        rop, q = params["reorder_point"].to_numpy(), params["order_qty"].to_numpy()
        position = on_hand + pipeline[t + 1:].sum(axis=0)
        order = np.where(position <= rop, np.ceil(np.maximum(q, rop + q - position)), 0)
        pipeline[t + pol.actual_lead_time_weeks] += order
        served = np.minimum(on_hand, D[t])
        lost = D[t] - served
        on_hand = on_hand - served
        for j, p in enumerate(prods):
            rows.append((weeks[t], p, D[t, j], served[j], lost[j], on_hand[j], order[j],
                         on_hand[j] * cost[j], rop[j]))
    sim = pd.DataFrame(rows, columns=["week", "product_id", "demand", "served", "lost_sales",
                                      "on_hand_end", "ordered", "stock_value", "reorder_point"])
    g = sim.groupby("product_id")
    summary = pd.DataFrame({
        "fill_rate": g["served"].sum() / g["demand"].sum().replace(0, np.nan),
        "stockout_weeks": g["lost_sales"].apply(lambda s: int((s > 0).sum())),
        "avg_stock_value": g["stock_value"].mean(),
        "on_hand_now": g["on_hand_end"].last(),
    })
    pending = pipeline[n_w:].sum(axis=0)
    summary["on_order_now"] = pd.Series(pending, index=prods)
    return sim, summary


def risk_table(summary: pd.DataFrame, next_mu: pd.Series, sigma: pd.Series, unit_cost: pd.Series,
               info: pd.DataFrame, pol: Policy = Policy()) -> pd.DataFrame:
    """Status and reorder recommendation per product as of the planning date."""
    params = reorder_parameters(next_mu, sigma.reindex(next_mu.index), unit_cost.reindex(next_mu.index), pol)
    t = params.join(summary[["on_hand_now", "on_order_now", "fill_rate", "stockout_weeks"]])
    t["position"] = t["on_hand_now"] + t["on_order_now"]
    t["weeks_of_cover"] = (t["on_hand_now"] / t["weekly_demand"].replace(0, np.nan)).round(1)
    after_lead = t["position"] - t["weekly_demand"] * pol.lead_time_weeks
    t["status"] = np.select(
        [after_lead < 0, t["position"] <= t["reorder_point"], t["weeks_of_cover"] > 12],
        ["Stockout risk", "Reorder now", "Overstocked"], default="Healthy")
    t["recommended_order"] = np.where(t["status"].isin(["Stockout risk", "Reorder now"]),
                                      np.ceil(np.maximum(t["order_qty"], t["reorder_point"] + t["order_qty"] - t["position"])), 0)
    t["stock_value_now"] = (t["on_hand_now"] * unit_cost.reindex(t.index)).round(0)
    t["order_value"] = (t["recommended_order"] * unit_cost.reindex(t.index)).round(0)
    return info.join(t, how="right").rename_axis("product_id").reset_index().sort_values(["status", "weeks_of_cover"])


SCENARIOS = {
    "Base plan": {},
    "Demand +20% (unplanned)": {"demand_multiplier": 1.2},
    "Demand -20% (unplanned)": {"demand_multiplier": 0.8},
    "Supplier lead time 2 -> 3 weeks": {"actual_lead_time_weeks": 3},
    "Demand +20% and lead time 3 weeks": {"demand_multiplier": 1.2, "actual_lead_time_weeks": 3},
}


def run_scenarios(demand, plan_mu, sigma, unit_cost, base: Policy = Policy()) -> pd.DataFrame:
    rows = []
    for name, change in SCENARIOS.items():
        pol = replace(base, **change)
        sim, summ = simulate(demand, plan_mu, sigma, unit_cost, pol)
        rows.append({
            "scenario": name,
            "fill_rate_pct": round(float(sim["served"].sum() / sim["demand"].sum() * 100), 1),
            "lost_units": int(round(sim["lost_sales"].sum())),
            "products_with_stockouts": int((summ["stockout_weeks"] > 0).sum()),
            "avg_inventory_value": int(round(sim.groupby("week")["stock_value"].sum().mean())),
        })
    return pd.DataFrame(rows)
