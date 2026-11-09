import datetime as dt

import pandas as pd
import pytest

from autogration_results import workbench
from autogration_results.engine import ResultStore, Unavailable


def _frame(rows):
    df = pd.DataFrame(rows, columns=["identifier", "identifier_type", "timestamp", "run_number", "property_name", "value", "unit"])
    df.insert(0, "system", "PROD")
    df.insert(0, "module", "GAP")
    return df


@pytest.fixture(scope="module")
def tiny():
    jan, feb = dt.date(2030, 1, 1), dt.date(2030, 2, 1)
    rows = []
    for well, oil, wat, gas_k in (("W1", 100.0, 0.0, 10.0), ("W2", 300.0, 100.0, 30.0)):
        for d in (jan, feb):
            rows += [
                (well, "WELL", d, 1, "AvgOilRate", oil, "Sm3/day"),
                (well, "WELL", d, 1, "OilRate", oil, "Sm3/day"),
                (well, "WELL", d, 1, "WatRate", wat, "Sm3/day"),
                (well, "WELL", d, 1, "AvgGasRate", gas_k, "kSm3/day"),
                (well, "WELL", d, 1, "ResPres", 200.0 if well == "W1" else 100.0, "BARa"),
            ]
    rows.append(("W1", "WELL", jan, 1, "CumOil", 0.0031, "MSm3"))
    rows.append(("W1", "WELL", feb, 1, "CumOil", 0.0059, "MSm3"))
    # The separator carries the same oil; well measures must not double count it.
    rows += [("SEP", "SEP", d, 1, "AvgOilRate", 400.0, "Sm3/day") for d in (jan, feb)]
    entities = pd.DataFrame({"identifier": ["W1", "W2"], "field": ["A", "B"], "working_interest": [0.5, 1.0]})
    return ResultStore.from_frames(_frame(rows), workbench.registry(), entities=entities)


def test_rate_sums_wells_only(tiny):
    df = tiny.query("oil_rate", ["period"], grain="month")
    assert df["value"].tolist() == pytest.approx([400.0, 400.0])


def test_volume_uses_days(tiny):
    total = tiny.query("oil_volume", [])["value"].iloc[0]
    assert total == pytest.approx(400 * (31 + 28) / 1e6)


def test_calendar_weighted_annual_rate(tiny):
    df = tiny.query("oil_rate", ["period"], grain="year")
    assert df["value"].iloc[0] == pytest.approx(400.0)


def test_ratio_is_ratio_of_sums(tiny):
    wct = tiny.query("wct", [])["value"].iloc[0]
    assert wct == pytest.approx(100 * 100 / 500)  # not mean(0 %, 25 %) = 12.5 %


def test_units_normalised(tiny):
    gas = tiny.query("gas_rate", ["period"])["value"].iloc[0]
    assert gas == pytest.approx(40.0)  # kSm3/d in, kSm3/d out via SI


def test_cumulative_takes_last(tiny):
    assert tiny.query("cum_oil", [])["value"].iloc[0] == pytest.approx(0.0059)


def test_state_mean(tiny):
    assert tiny.query("reservoir_pressure", [])["value"].iloc[0] == pytest.approx(150.0)


def test_entity_dimension_and_factor(tiny):
    by_field = tiny.query("oil_rate", ["entity.field"]).set_index("entity.field")["value"]
    assert by_field.to_dict() == pytest.approx({"A": 100.0, "B": 300.0})
    net = tiny.query("net_oe_volume_mmboe", [])["value"].iloc[0]
    gross = tiny.query("oe_volume_mmboe", [])["value"].iloc[0]
    assert 0 < net < gross


def test_unavailable_measure(tiny):
    with pytest.raises(Unavailable):
        tiny.query("water_injection_rate", [])
    with pytest.raises(Unavailable):
        tiny.query("oil_rate", ["entity.group"])
