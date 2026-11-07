"""Column lists from docs/CONTRACT.md. Keep in sync with that file (change both in one PR)."""

ZONES = ("north_scotland", "south_scotland", "england_wales")
SCOTTISH_ZONES = ("north_scotland", "south_scotland")
BOUNDARY_OF_ZONE = {"north_scotland": "B4", "south_scotland": "B6"}

FARMS_COLUMNS = [
    "bmu_id", "farm_id", "farm_name", "lat", "lon", "capacity_mw", "zone", "site_type", "repd_ref_id",
]

CURTAILMENT_COLUMNS = [
    "bmu_id", "farm_id", "zone", "period_start", "settlement_date", "settlement_period",
    "planned_mwh", "accepted_mwh", "curtailed_mwh_a", "potential_mwh_b", "curtailed_mwh_b",
    "metered_mwh", "bid_price_gbp_mwh", "wind_speed_100m_ms",
]

RESIDUAL_COLUMNS = [
    "zone", "period_start", "residual_mw", "residual_method", "boundary", "boundary_limit_mw",
    "boundary_flow_mw", "wind_delivered_mwh", "market_price_gbp_mwh", "gb_demand_mw",
]

FORECAST_COLUMNS = [
    "farm_id", "zone", "hour_start", "issued_at", "split", "curtailed_mwh_pred",
    "curtailed_mwh_p10", "curtailed_mwh_p90", "curtailed_mwh_actual",
    "baseline_persistence_mwh", "baseline_climatology_mwh", "baseline_wind_rule_mwh", "model_version",
]

SCHEDULES_COLUMNS = [
    "battery_id", "farm_id", "zone", "strategy", "dispatcher", "period_start", "plan_issued_at",
    "action", "power_mw", "soc_mwh", "captured_curtailment_mwh", "revenue_gbp", "reason",
]

# file name -> (columns, UTC timestamp columns)
PARQUET_FILES = {
    "curtailment.parquet": (CURTAILMENT_COLUMNS, ["period_start"]),
    "residual.parquet": (RESIDUAL_COLUMNS, ["period_start"]),
    "forecast.parquet": (FORECAST_COLUMNS, ["hour_start", "issued_at"]),
    "schedules.parquet": (SCHEDULES_COLUMNS, ["period_start", "plan_issued_at"]),
}
JSON_FILES = ("model_metrics.json", "scorecard.json")

STRATEGIES = ("do_nothing", "reactive", "forecast", "perfect")

# Constants used to cost waste (CONTRACT.md §7 and BRIEF.md lever 9/11 defaults)
MWH_PER_HOME_PER_YEAR = 2.7
DEFAULT_GAS_P_THERM = 80.0
DEFAULT_CARBON_GBP_T = 50.0
DEFAULT_CARBON_INTENSITY_T_MWH = 0.37


def replacement_gbp_mwh(gas_p_therm: float = DEFAULT_GAS_P_THERM,
                        carbon_gbp_t: float = DEFAULT_CARBON_GBP_T,
                        intensity_t_mwh: float = DEFAULT_CARBON_INTENSITY_T_MWH) -> float:
    """Cost of replacing 1 MWh of wasted wind with gas: fuel at 50% efficiency plus carbon."""
    return (gas_p_therm / 2.93) / 0.5 + carbon_gbp_t * intensity_t_mwh
