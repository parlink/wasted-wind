"""#49 full version: the live forecast covers the next 6 days (lead 1 = tomorrow, unchanged)."""
import numpy as np
import pandas as pd
import pytest

from app import data
from pipeline import features as ft
from pipeline import train as tr

NOW = pd.Timestamp("2026-11-08T09:37Z")


def test_week_days_are_tomorrow_and_the_five_after():
    days = tr.week_days(NOW)
    assert [k for k, _ in days] == list(ft.LEADS)
    assert days[0][1] == pd.Timestamp("2026-11-09T00:00Z") and days[-1][1] == pd.Timestamp("2026-11-14T00:00Z")


def test_lead_days_from_the_column_or_derived_from_the_issue_date():
    fc = pd.DataFrame({"hour_start": pd.to_datetime(["2026-05-02T05:00Z", "2026-11-09T05:00Z", "2026-11-12T23:00Z"]),
                       "issued_at": pd.to_datetime(["2026-05-01T10:00Z", "2026-11-08T09:37Z", "2026-11-08T09:37Z"])})
    assert data.lead_days(fc).tolist() == [1, 1, 4]                 # older files have no column
    assert data.lead_days(fc.assign(lead_days=[1, 1, 9])).tolist() == [1, 1, 9]


class _Const:
    """Stands in for a fitted model: predicts its lead number, and remembers the columns it saw."""
    def __init__(self, k):
        self.k, self.cols = k, None

    def predict(self, X):
        self.cols = list(X.columns)
        return np.full(len(X), float(self.k))


def _fake_inputs(now, day, n_days):
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv").drop_duplicates("farm_id").head(3)
    hours = pd.date_range(day, periods=24 * n_days, freq="h")
    weather = pd.concat([pd.DataFrame({"farm_id": f.farm_id, "zone": f.zone, "hour_start": hours, "wind_fc_ms": 11.0})
                         for f in farms.itertuples()], ignore_index=True)
    lim = pd.DataFrame({"B4": 2000.0, "B6": 5000.0}, index=pd.DatetimeIndex(hours[:24], name="hour_start"))
    dem = pd.DataFrame({"gb_demand_fc_mw": 30000.0, "publish_time": now - pd.Timedelta(hours=1)},
                       index=pd.DatetimeIndex(hours[:24], name="hour_start"))
    return weather, lim, dem


def test_live_week_rows_use_each_lead_model_and_only_what_is_known_now(monkeypatch):
    monkeypatch.setattr(tr, "live_inputs", _fake_inputs)
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv").drop_duplicates("farm_id").head(3)
    curt = pd.concat([pd.DataFrame({"farm_id": f.farm_id, "zone": f.zone,
                                    "period_start": pd.date_range("2026-09-01T00:00Z", NOW.floor("D"), freq="30min"),
                                    "curtailed_mwh_a": 1.0, "bid_price_gbp_mwh": -20.0}) for f in farms.itertuples()])
    models = {k: {c: _Const(k) for c in ["curtailed_mwh_pred", *tr.QUANTILES]} for k in ft.LEADS}
    clim = pd.Series(dtype=float)
    base = {"clim": clim, "clim_fb": clim, "rule": {"threshold_mw": 1e9, "level_mwh": 0.0}, "rule_level": clim}
    rows = tr.live_week_rows(models, {k: base for k in ft.LEADS}, curt, NOW)

    assert (rows.issued_at == NOW).all()
    assert sorted(rows.lead_days.unique()) == list(ft.LEADS)
    day = rows.hour_start.dt.normalize()
    assert ((day - NOW.normalize()).dt.days == rows.lead_days).all()   # lead k = the day k days on
    assert (rows.curtailed_mwh_pred == rows.lead_days).all()            # each day used its own lead's model
    assert models[1]["curtailed_mwh_pred"].cols == ft.FEATURES
    assert models[3]["curtailed_mwh_pred"].cols == tr.lead_features(3)  # no NESO limits beyond tomorrow
    assert rows.loc[rows.lead_days > 1, "b4_limit_mw"].isna().all()
    ft.check_no_leakage(rows)
    assert rows.groupby("lead_days").size().nunique() == 1              # every day complete


def test_days_after_tomorrow_have_no_band_without_quantile_models(monkeypatch):
    # #49: only the day-ahead band is calibrated; leads 2+ carry the point forecast and a null band
    monkeypatch.setattr(tr, "live_inputs", _fake_inputs)
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv").drop_duplicates("farm_id").head(3)
    curt = pd.concat([pd.DataFrame({"farm_id": f.farm_id, "zone": f.zone,
                                    "period_start": pd.date_range("2026-09-01T00:00Z", NOW.floor("D"), freq="30min"),
                                    "curtailed_mwh_a": 1.0, "bid_price_gbp_mwh": -20.0}) for f in farms.itertuples()])
    models = {1: {c: _Const(1) for c in ["curtailed_mwh_pred", *tr.QUANTILES]},
              **{k: {"curtailed_mwh_pred": _Const(k)} for k in ft.LEADS[1:]}}
    clim = pd.Series(dtype=float)
    base = {"clim": clim, "clim_fb": clim, "rule": {"threshold_mw": 1e9, "level_mwh": 0.0}, "rule_level": clim}
    rows = tr.live_week_rows(models, {k: base for k in ft.LEADS}, curt, NOW)
    later = rows[rows.lead_days > 1]
    assert later.curtailed_mwh_p10.isna().all() and later.curtailed_mwh_p90.isna().all()
    assert rows.loc[rows.lead_days == 1, "curtailed_mwh_p90"].notna().all()
