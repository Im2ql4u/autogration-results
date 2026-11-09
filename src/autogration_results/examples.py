"""Synthetic example study in Autogration's result layout.

Fictional field "Example": 3 fields, 15 producers/injectors, 2 separators, 8 cases from a
2 x 2 x 2 assumption grid, monthly for 15 years. It contains deliberate problems so the Health
homepage has something to show: a failed case, an early-terminated case, a collapse (shut-in),
GAP no-value sentinels and a negative rate.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2027-01-01")
MONTHS = 180
SENTINEL = 3.4e35


@dataclass(frozen=True)
class Well:
    name: str
    field: str
    group: str
    segment: str
    kind: str  # oil, gas, inj
    separator: str
    license: str
    start: int
    plateau: float  # Sm3/d oil (oil/gas wells) or water injection
    gor0: float
    infill: bool = False


WELLS = [
    Well("A-P1", "Alpha", "Alpha Main", "Upper", "oil", "HP_SEP", "PL101", 0, 2200, 120),
    Well("A-P2", "Alpha", "Alpha Main", "Upper", "oil", "HP_SEP", "PL101", 0, 1900, 110),
    Well("A-P3", "Alpha", "Alpha Main", "Lower", "oil", "HP_SEP", "PL101", 6, 1700, 140),
    Well("A-P4", "Alpha", "Alpha Main", "Lower", "oil", "HP_SEP", "PL101", 12, 1500, 150),
    Well("A-P5", "Alpha", "Alpha North", "North", "oil", "HP_SEP", "PL102", 18, 1300, 160),
    Well("A-P6", "Alpha", "Alpha North", "North", "oil", "HP_SEP", "PL102", 30, 1100, 170),
    Well("A-P7", "Alpha", "Alpha North", "North", "oil", "HP_SEP", "PL102", 48, 900, 180, infill=True),
    Well("B-P1", "Bravo", "Bravo", "Gas Cap", "gas", "HP_SEP", "PL201", 0, 400, 2500),
    Well("B-P2", "Bravo", "Bravo", "Gas Cap", "gas", "HP_SEP", "PL201", 3, 350, 2800),
    Well("B-P3", "Bravo", "Bravo", "Rim", "oil", "HP_SEP", "PL201", 9, 1200, 300),
    Well("B-P4", "Bravo", "Bravo", "Rim", "oil", "HP_SEP", "PL201", 60, 800, 320, infill=True),
    Well("C-P1", "Charlie", "Charlie Satellite", "Main", "oil", "SAT_SEP", "PL301", 24, 1600, 90),
    Well("C-P2", "Charlie", "Charlie Satellite", "Main", "oil", "SAT_SEP", "PL301", 30, 1400, 95),
    Well("A-I1", "Alpha", "Alpha Main", "Upper", "inj", "HP_SEP", "PL101", 6, 3500, 0),
    Well("A-I2", "Alpha", "Alpha North", "North", "inj", "HP_SEP", "PL102", 24, 2500, 0),
    Well("B-I1", "Bravo", "Bravo", "Rim", "inj", "HP_SEP", "PL201", 12, 2000, 0),
]
SEPARATORS = {"HP_SEP": ("Alpha", "Main platform"), "SAT_SEP": ("Charlie", "Satellite")}
WORKING_INTEREST = {"PL101": 0.65, "PL102": 0.5, "PL201": 0.8, "PL301": 0.4}

AXES = {"sep_pressure": ["30", "20"], "gas_lift": ["off", "on"], "infill": ["no", "yes"]}


def _cases() -> pd.DataFrame:
    rows = []
    for run, (sep, gl, infill) in enumerate(itertools.product(*AXES.values()), start=1):
        # Labels must not equal result variable names (e.g. GasLift), or the leak test cannot tell them apart.
        parts = [p for p, on in (("Low sep", sep == "20"), ("Gas lift", gl == "on"), ("Infill wells", infill == "yes")) if on]
        rows.append(
            {
                "run_number": run,
                "case_name": " + ".join(parts) if parts else "Base",
                "task_id": f"example-task-{run:03d}",
                "status": "Completed",
                "is_baseline": run == 1,
                "description": "Synthetic example case",
                "sep_pressure": sep,
                "gas_lift": gl,
                "infill": infill,
            }
        )
    cases = pd.DataFrame(rows)
    cases.loc[cases.run_number == 7, ["status", "description"]] = ["Failed", "Synthetic: node client error, no results"]
    return cases


def _well_series(w: Well, case: pd.Series, rng: np.random.Generator) -> dict[str, np.ndarray]:
    t = np.arange(MONTHS)
    days = np.array([(START + pd.DateOffset(months=int(i))).days_in_month for i in t])
    active = t >= w.start
    if w.infill and case.infill == "no":
        active[:] = False
    tau = np.clip(t - w.start, 0, None)
    uptime = np.where(active, 0.92 + 0.05 * rng.random(MONTHS), 0.0)
    sep_boost = 1.06 if case.sep_pressure == "20" else 1.0
    out: dict[str, np.ndarray] = {}

    if w.kind == "inj":
        inj = np.where(active, w.plateau * np.minimum(1, (tau + 1) / 6), 0.0)
        out["WatInjRate"] = inj
        out["AvgWatInjRate"] = inj * uptime
        out["CumWatInj"] = np.cumsum(inj * uptime * days) / 1e6
        out["BHP"] = np.where(active, 380 + 0.01 * inj, 0.0)
        out["WHP"] = np.where(active, 180 + 0.005 * inj, 0.0)
        return out

    decline = 0.022 * (0.8 if case.gas_lift == "on" else 1.0)
    plateau_months = 24 if w.kind == "oil" else 36
    q = w.plateau * sep_boost * (1.03 if case.gas_lift == "on" else 1.0)
    oil = np.where(tau < plateau_months, q, q / (1 + 0.5 * decline * (tau - plateau_months)) ** 2)
    oil = np.where(active, oil * (1 + 0.02 * rng.standard_normal(MONTHS)), 0.0)
    cum_frac = np.cumsum(oil * days) / max(np.sum(oil * days), 1)
    gor = w.gor0 * (1 + 0.8 * cum_frac**2)
    gas = oil * gor
    wct = np.where(active, 0.9 / (1 + np.exp(-(tau - 60) / 18)), 0.0) if w.kind == "oil" else 0.05 * cum_frac
    water = oil * wct / (1 - wct)
    gas_lift = np.where(active & (case.gas_lift == "on") & (w.kind == "oil"), 80_000.0, 0.0)
    res_pres = 320 - 110 * cum_frac
    sep_p = float(case.sep_pressure)

    out["OilRate"], out["GasRate"], out["WatRate"] = oil, gas, water
    out["LiqRate"] = oil + water
    for p in ("OilRate", "GasRate", "WatRate", "LiqRate"):
        out[f"Avg{p}"] = out[p] * uptime
    out["CumOil"] = np.cumsum(out["AvgOilRate"] * days) / 1e6
    out["CumGas"] = np.cumsum(out["AvgGasRate"] * days) / 1e6
    out["CumWat"] = np.cumsum(out["AvgWatRate"] * days) / 1e6
    out["GOR"] = np.where(active, gor, 0.0)
    out["WCT"] = np.where(active, wct * 100, 0.0)
    out["ResPres"] = np.where(active, res_pres, 0.0)
    out["BHP"] = np.where(active, res_pres - 0.02 * (oil + water), 0.0)
    out["WHP"] = np.where(active, sep_p + 12 + 0.002 * (oil + water), 0.0)
    out["WHT"] = np.where(active, 45 + 25 * (oil + water) / (w.plateau * 2), 0.0)
    out["ManPres"] = np.where(active, sep_p + 5, 0.0)
    out["GasLift"] = gas_lift
    out["AvgGasLift"] = gas_lift * uptime
    return out


UNITS = {
    **dict.fromkeys(
        ["OilRate", "GasRate", "WatRate", "LiqRate", "AvgOilRate", "AvgGasRate", "AvgWatRate", "AvgLiqRate",
         "WatInjRate", "AvgWatInjRate", "GasLift", "AvgGasLift"],
        "Sm3/day",
    ),
    **dict.fromkeys(["CumOil", "CumGas", "CumWat", "CumWatInj"], "MSm3"),
    **dict.fromkeys(["ResPres", "BHP", "WHP", "ManPres", "Pres"], "BARa"),
    **dict.fromkeys(["WHT", "Temp"], "deg C"),
    "GOR": "Sm3/Sm3",
    "WCT": "percent",
}


def _case_results(case: pd.Series) -> pd.DataFrame:
    rng = np.random.default_rng(1000 + int(case.run_number))
    dates = [(START + pd.DateOffset(months=i)).date() for i in range(MONTHS)]
    frames = []
    sep_tot: dict[str, dict[str, np.ndarray]] = {s: {} for s in SEPARATORS}
    for w in WELLS:
        series = _well_series(w, case, rng)
        if case.run_number == 6 and w.field == "Charlie":
            # Synthetic collapse: satellite wells shut in from mid-2033.
            cut = 77
            for k in series:
                if not k.startswith("Cum"):
                    series[k][cut:] = 0.0
                else:
                    series[k][cut:] = series[k][cut - 1]
        if w.kind != "inj":
            for p in ("OilRate", "GasRate", "WatRate", "AvgOilRate", "AvgGasRate", "AvgWatRate"):
                sep_tot[w.separator][p] = sep_tot[w.separator].get(p, 0) + series[p]
        frames.append(_long(series, w.name, "WELL", dates, case.run_number))
    for s, totals in sep_tot.items():
        totals["Pres"] = float(case.sep_pressure) + 0.3 * rng.standard_normal(MONTHS)
        totals["Temp"] = 55 + 10 * totals["OilRate"] / max(totals["OilRate"].max(), 1)
        frames.append(_long(totals, s, "SEP", dates, case.run_number))
    df = pd.concat(frames, ignore_index=True)

    if case.run_number == 3:
        bad = (df.identifier == "SAT_SEP") & (df.property_name == "Temp") & (df.timestamp >= dates[40]) & (df.timestamp < dates[46])
        df.loc[bad, "value"] = SENTINEL
    if case.run_number == 4:
        df.loc[(df.identifier == "B-P3") & (df.property_name == "WatRate") & (df.timestamp == dates[100]), "value"] = -12.5
    if case.run_number == 5:
        df = df[df.timestamp < dates[96]]
    return df


def _long(series: dict[str, np.ndarray], name: str, typ: str, dates: list, run: int) -> pd.DataFrame:
    parts = [
        pd.DataFrame({"timestamp": dates, "property_name": p, "value": v.astype(float), "unit": UNITS.get(p)})
        for p, v in series.items()
    ]
    df = pd.concat(parts, ignore_index=True)
    df.insert(0, "identifier_type", typ)
    df.insert(0, "identifier", name)
    df.insert(0, "system", "PROD")
    df.insert(0, "module", "GAP")
    df["run_number"] = np.int32(run)
    return df[["module", "system", "identifier", "identifier_type", "timestamp", "run_number", "property_name", "value", "unit"]]


def _entities() -> pd.DataFrame:
    rows = [
        {
            "identifier": w.name,
            "identifier_type": "WELL",
            "field": w.field,
            "group": w.group,
            "segment": w.segment,
            "well_type": {"oil": "Oil producer", "gas": "Gas producer", "inj": "Water injector"}[w.kind],
            "license": w.license,
            "separator": w.separator,
            "infill": "yes" if w.infill else "no",
            "working_interest": WORKING_INTEREST[w.license],
            "oil_sales_factor": 1.0,
            "gas_sales_factor": 1.0,
        }
        for w in WELLS
    ]
    rows += [
        {"identifier": s, "identifier_type": "SEP", "field": f, "group": label, "well_type": "Separator"}
        for s, (f, label) in SEPARATORS.items()
    ]
    return pd.DataFrame(rows)


def write_example(folder: Path) -> Path:
    """Write the example study as Autogration task Parquet + cases.csv + entities.csv."""
    (folder / "results").mkdir(parents=True, exist_ok=True)
    cases = _cases()
    for case in cases.itertuples():
        if case.status != "Completed":
            continue
        df = _case_results(pd.Series(case._asdict()))
        df.to_parquet(folder / "results" / f"{case.task_id}.parquet", index=False)
    cases.to_csv(folder / "cases.csv", index=False)
    _entities().to_csv(folder / "entities.csv", index=False)
    return folder


def ensure_example(folder: Path) -> Path:
    if not (folder / "cases.csv").exists():
        write_example(folder)
    return folder
