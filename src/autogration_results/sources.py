"""Adapters that turn other result layouts into the canonical contract.

``load_gap_folder`` reads the layout behind the Yggdrasil Power BI report
(S:\\YGGDRASIL\\IPM\\User\\vlavol\\YGG_PowerBI_share): wide GAP CSV exports per model version
plus Cases.csv, Wells.csv, Separators.csv and Licenses.csv. Runs locally only; nothing is uploaded.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from autogration_results.engine import ResultStore, _q
from autogration_results.measures import MeasureRegistry

# folder -> (identifier_type, identifier column)
DEFAULT_GAP_TABLES: dict[str, tuple[str, str]] = {
    "Yggdrasil_GAP_results": ("WELL", "Well"),
    "Yggdrasil_GAP_Separators": ("SEP", "Separator"),
}

_RATE = {"OilRate", "WatRate", "LiqRate", "WatInjRate", "AvgOilRate", "AvgWatRate", "AvgLiqRate", "AvgWatInjRate"}
_GAS_RATE = {"GasRate", "GasLift", "TotalGasRate", "AvgGasRate", "AvgGasLift"}
# Units as used by the Power BI measures (e.g. GOR = GasRate * 1000 / OilRate).
GAP_UNITS: dict[str, str] = {
    **{c: "Sm3/d" for c in _RATE},
    **{c: "kSm3/d" for c in _GAS_RATE},
    **{f"{c}_In": "Sm3/d" for c in ("OilRate", "WatRate", "AvgOilRate", "AvgWatRate")},
    **{f"{c}_In": "kSm3/d" for c in ("GasRate", "AvgGasRate")},
    "CumOil": "MSm3", "CumWat": "MSm3", "CumWatInj": "MSm3", "CumGas": "GSm3",
    "BHP": "bara", "WHP": "bara", "ResPres": "bara", "ManPres": "bara", "Pressure": "bara",
    "WHT": "degC", "ManTemp": "degC", "Temperature": "degC",
    "Uptime": "fraction",
}
# GAP separator files name pressure/temperature/rates differently from Autogration's separator results.
GAP_RENAMES = {
    "SEP": {
        "Pressure": "Pres",
        "Temperature": "Temp",
        **{f"{c}_In": c for c in ("OilRate", "GasRate", "WatRate", "AvgOilRate", "AvgGasRate", "AvgWatRate")},
    }
}


def _read_csv(path: Path, sep: str | None = None) -> pd.DataFrame:
    if sep is None:
        sep = "\t" if "\t" in path.open(encoding="cp1252").readline() else ","
    return pd.read_csv(path, sep=sep, encoding="cp1252")


def load_gap_folder(
    root: Path,
    registry: MeasureRegistry,
    tables: dict[str, tuple[str, str]] = DEFAULT_GAP_TABLES,
) -> ResultStore:
    con = duckdb.connect()
    parts = []
    for folder, (identifier_type, id_col) in tables.items():
        files = sorted((root / folder).glob("*.csv"))
        if not files:
            continue
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _wide AS SELECT * FROM read_csv(?, union_by_name = true, filename = true, header = true, sample_size = -1)",
            [[str(f) for f in files]],
        )
        cols = [r[0] for r in con.execute("DESCRIBE _wide").fetchall()]
        date_col = "DateNum" if "DateNum" in cols else "Date"
        keys = {"Case_ID", id_col, date_col, "filename"}
        renames = GAP_RENAMES.get(identifier_type, {})
        values = [c for c in cols if c not in keys]
        casts = ", ".join(f"TRY_CAST({_q(c)} AS DOUBLE) AS {_q(renames.get(c, c))}" for c in values)
        unit_cases = " ".join(f"WHEN '{renames.get(c, c)}' THEN '{GAP_UNITS[c]}'" for c in values if c in GAP_UNITS)
        con.execute(
            f"""CREATE OR REPLACE TEMP TABLE _long_{identifier_type} AS
            SELECT parse_filename(filename, true) AS module, 'PROD' AS system,
                   CAST({_q(id_col)} AS VARCHAR) AS identifier, '{identifier_type}' AS identifier_type,
                   CAST(DATE '1899-12-30' + CAST(round({_q(date_col)}) AS INTEGER) AS DATE) AS timestamp,
                   CAST(Case_ID AS INTEGER) AS run_number, property_name, value,
                   CASE property_name {unit_cases} ELSE NULL END AS unit
            FROM (UNPIVOT (SELECT filename, Case_ID, {_q(id_col)}, {_q(date_col)}, {casts} FROM _wide)
                  ON COLUMNS(* EXCLUDE (filename, Case_ID, {_q(id_col)}, {_q(date_col)}))
                  INTO NAME property_name VALUE value)"""
        )
        parts.append(f"SELECT * FROM _long_{identifier_type}")
    if not parts:
        raise FileNotFoundError(f"no GAP result CSVs under {root}")
    con.execute("CREATE TABLE raw_results AS " + " UNION ALL ".join(parts))

    cases = None
    if (root / "Cases.csv").exists():
        c = _read_csv(root / "Cases.csv")
        cases = c.rename(columns={"Case_ID": "run_number", "CaseName": "case_name", "Case Description": "description"})
        cases = cases.drop_duplicates("run_number")

    entities = []
    if (root / "Wells.csv").exists():
        w = _read_csv(root / "Wells.csv").rename(columns={"Well": "identifier"}).assign(identifier_type="WELL")
        if (root / "Licenses.csv").exists() and "License" in w:
            lic = _read_csv(root / "Licenses.csv").rename(columns={"AkerBP_WI": "working_interest"})
            w = w.merge(lic, on="License", how="left")
        entities.append(w)
    if (root / "Separators.csv").exists():
        s = _read_csv(root / "Separators.csv").rename(columns={"Separator_ID": "identifier"}).assign(identifier_type="SEP")
        entities.append(s)
    ent = pd.concat(entities, ignore_index=True) if entities else None
    if ent is not None:
        ent.columns = [str(c).strip() for c in ent.columns]
        ent = ent.drop_duplicates(["identifier", "identifier_type"])
        obj = ent.select_dtypes("object").columns
        ent[obj] = ent[obj].astype("string")

    return ResultStore._finish(con, registry, cases, ent, str(root))
