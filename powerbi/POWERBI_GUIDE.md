# Power BI dashboard: build guide

`python run.py` writes a star schema to `powerbi/data/`. This guide turns it into the Power BI report (about 30 minutes in Power BI Desktop).

## 1. Load the data

**Get data → Text/CSV** and load every file in `powerbi/data/`:

| Table | Grain | Used for |
| --- | --- | --- |
| `dim_product` | one row per product | product, category, department, unit cost, `core_product` flag |
| `fact_weekly_demand` | week × product × market | demand trends, revenue, margin |
| `fact_forecast_backtest` | origin × week × product × method | forecast accuracy |
| `fact_inventory_simulation` | week × product | inventory value, fill rate, stockouts |
| `reorder_recommendations` | one row per core product | status, reorder point, order quantity |
| `fact_delivery` | month × market × shipping mode × category | delivery performance |
| `scenarios` | one row per scenario | what-if results |
| `forecast_accuracy` | one row per method | accuracy table |

Create a date table: **Modeling → New table**

```DAX
dim_date = ADDCOLUMNS ( CALENDAR ( DATE ( 2014, 12, 29 ), DATE ( 2018, 2, 5 ) ),
    "Year", YEAR ( [Date] ), "Month", FORMAT ( [Date], "MMM" ), "MonthNo", MONTH ( [Date] ),
    "YearMonth", FORMAT ( [Date], "YYYY-MM" ) )
```

Mark it as the date table, then sort `Month` by `MonthNo`.

## 2. Relationships (Model view)

- `dim_product[product_id]` 1 → * `product_id` in `fact_weekly_demand`, `fact_forecast_backtest`, `fact_inventory_simulation`, `reorder_recommendations`
- `dim_date[Date]` 1 → * `fact_weekly_demand[week]`, `fact_forecast_backtest[week]`, `fact_inventory_simulation[week]`, `fact_delivery[month]`

## 3. Measures

Create a `_Measures` table and paste the measures from [`measures.dax`](measures.dax).

## 4. Report pages

| Page | Visuals | Measures |
| --- | --- | --- |
| **Overview** | 6 KPI cards; monthly units column chart; late rate line | `Units YoY %`, `Forecast Accuracy %`, `On-Time Rate %`, `Inventory Value Now`, `Products to Reorder`, `Fill Rate %` |
| **Demand trends** | units by YearMonth (line), units by Month (seasonality), units by department (bar); slicers: market, department | `Units`, `Units LY`, `Units YoY %` |
| **Forecast accuracy** | forecast vs actual by week (line, slicer on `method`); table of `forecast_accuracy` | `WAPE %`, `Forecast Bias %` |
| **Inventory risk** | inventory value by week (area); reorder table with conditional formatting on `status`; cover by product (bar) | `Avg Weekly Inventory Value`, `Products at Stockout Risk`, `Reorder Value`, `Weeks of Cover` |
| **Delivery performance** | late rate by shipping mode (bar); promised vs actual days (clustered bar); late rate by market (bar); late rate by month (line) | `Late Rate %`, `Avg Promised Days`, `Avg Actual Days` |
| **Reorder recommendations** | table: product, status, weekly demand, on hand, on order, reorder point, recommended order, order value; filter status = "Reorder now" / "Stockout risk" | `Reorder Value` |
| **What-if scenarios** | fill rate by scenario (bar), lost units (bar), scenario table | `Scenario Fill Rate %`, `Fill Rate vs Base (pts)` |

Conditional formatting for `status`: Stockout risk = red, Reorder now = amber, Healthy = green, Overstocked = blue.

Save as `powerbi/Supply_Chain_Planner.pbix`.
