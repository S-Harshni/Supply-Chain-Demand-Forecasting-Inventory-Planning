"""Weekly demand, trend and seasonality, and a backtested demand forecast."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .data import CATALOGUE_BREAK

LAGS = [1, 2, 3, 4, 8, 13, 26, 52]
HORIZON = 4          # forecast the next 4 weeks
TEST_WEEKS = 28      # rolling-origin backtest over the last 28 weeks (7 origins)
ORIGIN_STEP = 4


def week_start(s: pd.Series) -> pd.Series:
    d = s.dt.normalize()
    return d - pd.to_timedelta(d.dt.weekday, unit="D")


def weekly_matrix(lines: pd.DataFrame, by_market: bool = False) -> pd.DataFrame:
    """Units per complete week (rows) before the catalogue break.

    Columns are products, or "product_id|market" series when by_market is True
    (each regional market plans its own stock).
    """
    d = lines[lines["is_demand"] & ~lines["after_catalogue_break"]].copy()
    d["week"] = week_start(d["order_date"])
    if by_market:
        d["product_id"] = d["product_id"].astype(str) + "|" + d["market"]
    # Only complete weeks: skip a partial first week and the week cut by the break.
    first = d["week"].min()
    if d["order_date"].min().normalize() > first:
        first += pd.Timedelta(days=7)
    last = CATALOGUE_BREAK - pd.Timedelta(days=7)
    last = last - pd.Timedelta(days=last.weekday())
    if last + pd.Timedelta(days=7) > CATALOGUE_BREAK:
        last -= pd.Timedelta(days=7)
    m = d.pivot_table(index="week", columns="product_id", values="quantity", aggfunc="sum", fill_value=0)
    weeks = pd.date_range(first, last, freq="7D")
    return m.reindex(weeks, fill_value=0).astype(float)


def forecastable(weekly: pd.DataFrame, window: int = 52, min_active_share: float = 0.9) -> list:
    """Series still in the range: sold in at least 90% of the last 52 weeks.

    Products discontinued or launched during 2017 fail this test and are
    reported as assortment changes instead of being forecast.
    """
    recent = weekly.iloc[-window:]
    share = (recent > 0).mean()
    return sorted(share[share >= min_active_share].index)


def assortment_changes(weekly: pd.DataFrame) -> dict:
    """When products start and stop selling before the catalogue break."""
    first = weekly.apply(lambda s: s[s > 0].index.min())
    last = weekly.apply(lambda s: s[s > 0].index.max())
    end = weekly.index[-1]
    return {
        "products_discontinued_2017": int(((last < end - pd.Timedelta(weeks=8)) & (last.dt.year == 2017)).sum()),
        "products_launched_2017": int((first.dt.year == 2017).sum()),
        "products_selling_throughout": int(((first <= weekly.index[4]) & (last >= end - pd.Timedelta(weeks=1))).sum()),
    }


def trend_and_seasonality(weekly: pd.DataFrame, products: pd.DataFrame) -> dict:
    monthly = weekly.sum(axis=1).resample("MS").sum()
    full_years = monthly[monthly.index.year.isin([2015, 2016])]
    yearly = monthly.groupby(monthly.index.year).sum()
    jan_sep = monthly[monthly.index.month <= 9].groupby(monthly[monthly.index.month <= 9].index.year).sum()
    # Average weekly demand per calendar month, so months with five week-starts
    # do not look busier than months with four.
    wk = weekly.sum(axis=1)
    wk = wk[wk.index.year.isin([2015, 2016])]
    by_month = wk.groupby(wk.index.month).mean()
    season_index = (by_month / wk.mean()).round(3)

    dept = products.set_index("product_id")["department"]
    dm = weekly.T.groupby(dept.reindex(weekly.columns).values).sum().T.resample("MS").sum()
    dept_growth = ((dm[dm.index.year == 2017].iloc[:9].sum() / dm[dm.index.year == 2016].iloc[:9].sum() - 1) * 100).round(1)
    return {
        "monthly_units": monthly,
        "season_index": season_index,
        "units_by_year": yearly,
        "jan_sep_growth_2017_vs_2016_pct": round(float(jan_sep.loc[2017] / jan_sep.loc[2016] - 1) * 100, 1),
        "growth_2016_vs_2015_pct": round(float(yearly.loc[2016] / yearly.loc[2015] - 1) * 100, 1),
        "peak_month": int(season_index.idxmax()),
        "low_month": int(season_index.idxmin()),
        "department_growth_pct": dept_growth.sort_values(),
    }


def _features(weekly: pd.DataFrame, prods: list, origin: int, h: int) -> pd.DataFrame:
    """Features known at week `origin` for predicting week origin + h."""
    y = weekly[prods].to_numpy()
    rows = []
    target_week = weekly.index[0] + pd.Timedelta(weeks=origin + h)
    woy = int(target_week.isocalendar().week)
    for j, p in enumerate(prods):
        hist = y[: origin + 1, j]
        f = {"product_id": p, "h": h, "week_of_year": woy}
        for lag in LAGS:
            f[f"lag_{lag}"] = hist[-lag] if len(hist) >= lag else np.nan
        for w in (4, 13, 26):
            f[f"ma_{w}"] = hist[-w:].mean() if len(hist) >= w else np.nan
        f["std_13"] = hist[-13:].std() if len(hist) >= 13 else np.nan
        f["zero_share_13"] = (hist[-13:] == 0).mean() if len(hist) >= 13 else np.nan
        f["same_week_last_year"] = y[origin + h - 52, j] if origin + h - 52 >= 0 else np.nan
        rows.append(f)
    return pd.DataFrame(rows)


def _train_set(weekly: pd.DataFrame, prods: list, last_target: int) -> tuple[pd.DataFrame, np.ndarray]:
    y = weekly[prods].to_numpy()
    X, t = [], []
    for origin in range(52, last_target):
        for h in range(1, HORIZON + 1):
            if origin + h > last_target:
                break
            X.append(_features(weekly, prods, origin, h))
            t.append(y[origin + h])
    return pd.concat(X, ignore_index=True), np.concatenate(t)


def _model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(loss="poisson", max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=31, min_samples_leaf=40, random_state=0)


FEATURES_DROP = ["product_id"]


def baselines(weekly: pd.DataFrame, prods: list, origin: int) -> dict[str, np.ndarray]:
    """Simple rules every planner would try first. Shape: (HORIZON, n_products)."""
    y = weekly[prods].to_numpy()
    hist = y[: origin + 1]
    out = {
        "Last week": np.repeat(hist[-1:], HORIZON, axis=0),
        "4-week average": np.repeat(hist[-4:].mean(axis=0, keepdims=True), HORIZON, axis=0),
        "13-week average": np.repeat(hist[-13:].mean(axis=0, keepdims=True), HORIZON, axis=0),
        "Same week last year": np.stack([y[origin + h - 52] for h in range(1, HORIZON + 1)]),
    }
    return out


def backtest(weekly: pd.DataFrame, prods: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling-origin backtest. The model only ever sees weeks up to the origin."""
    n = len(weekly)
    origins = list(range(n - TEST_WEEKS - 1, n - HORIZON, ORIGIN_STEP))
    y = weekly[prods].to_numpy()
    records = []
    for origin in origins:
        X, t = _train_set(weekly, prods, origin)
        model = _model().fit(X.drop(columns=FEATURES_DROP), t)
        preds = {"Gradient boosting": np.stack([
            model.predict(_features(weekly, prods, origin, h).drop(columns=FEATURES_DROP))
            for h in range(1, HORIZON + 1)])}
        preds.update(baselines(weekly, prods, origin))
        for method, f in preds.items():
            for h in range(1, HORIZON + 1):
                for j, p in enumerate(prods):
                    records.append((weekly.index[origin], weekly.index[origin + h], h, p, method,
                                    float(f[h - 1, j]), float(y[origin + h, j])))
    bt = pd.DataFrame(records, columns=["origin", "week", "horizon", "product_id", "method", "forecast", "actual"])
    return bt, score(bt)


def wape(actual, forecast) -> float:
    return float(np.abs(np.asarray(actual) - np.asarray(forecast)).sum() / np.asarray(actual).sum())


def score(bt: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for method, g in bt.groupby("method"):
        four = g.groupby(["origin", "product_id"])[["forecast", "actual"]].sum()
        rows.append({
            "method": method,
            "wape_weekly_pct": round(wape(g["actual"], g["forecast"]) * 100, 1),
            "wape_4week_total_pct": round(wape(four["actual"], four["forecast"]) * 100, 1),
            "bias_pct": round(float((g["forecast"].sum() / g["actual"].sum() - 1) * 100), 1),
        })
    return pd.DataFrame(rows).sort_values("wape_4week_total_pct").reset_index(drop=True)


def forecast_next(weekly: pd.DataFrame, prods: list) -> pd.DataFrame:
    """Forecast the 4 weeks after the last complete week, using all history."""
    n = len(weekly)
    X, t = _train_set(weekly, prods, n - 1)
    model = _model().fit(X.drop(columns=FEATURES_DROP), t)
    rows = []
    for h in range(1, HORIZON + 1):
        f = model.predict(_features(weekly, prods, n - 1, h).drop(columns=FEATURES_DROP))
        wk = weekly.index[-1] + pd.Timedelta(weeks=h)
        rows += [(wk, h, p, float(v)) for p, v in zip(prods, f)]
    return pd.DataFrame(rows, columns=["week", "horizon", "product_id", "forecast"])


def forecast_next_with(method: str, weekly: pd.DataFrame, prods: list) -> pd.DataFrame:
    """Next-4-week forecast from the chosen (champion) method."""
    if method == "Gradient boosting":
        return forecast_next(weekly, prods)
    n = len(weekly)
    padded = pd.concat([weekly[prods], pd.DataFrame(0.0, index=pd.date_range(
        weekly.index[-1] + pd.Timedelta(weeks=1), periods=HORIZON, freq="7D"), columns=prods)])
    f = baselines(padded, prods, n - 1)[method]
    rows = []
    for h in range(1, HORIZON + 1):
        wk = weekly.index[-1] + pd.Timedelta(weeks=h)
        rows += [(wk, h, p, float(v)) for p, v in zip(prods, f[h - 1])]
    return pd.DataFrame(rows, columns=["week", "horizon", "product_id", "forecast"])
