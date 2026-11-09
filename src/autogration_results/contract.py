"""Canonical data contract.

Results use Autogration's per-task Parquet layout (``GET /runs/{task_id}/results/parquet``):
one row per equipment x property x timestep. Cases and entities are small side tables.
"""

from __future__ import annotations

RESULT_COLUMNS: dict[str, str] = {
    "module": "VARCHAR",
    "system": "VARCHAR",
    "identifier": "VARCHAR",
    "identifier_type": "VARCHAR",
    "timestamp": "DATE",
    "run_number": "INTEGER",
    "property_name": "VARCHAR",
    "value": "DOUBLE",
    "unit": "VARCHAR",
}

# cases.csv: one row per case (= Autogration task / assumption case). Extra columns are assumption axes.
CASE_REQUIRED = ["run_number", "case_name"]
CASE_RESERVED = {"run_number", "case_name", "task_id", "status", "is_baseline", "description"}

# entities.csv: one row per result identifier. Extra columns become `entity.<column>` dimensions.
ENTITY_REQUIRED = ["identifier"]
ENTITY_RESERVED = {"identifier", "identifier_type"}

COMPLETED = "Completed"

# GAP/RESOLVE write 3.4e+35 for "no value".
SENTINEL_ABS = 1e35

# Native unit -> (SI unit used by measures, factor). Unknown units pass through unchanged.
UNIT_TO_SI: dict[str, tuple[str, float]] = {
    "Sm3/day": ("Sm3/d", 1.0),
    "Sm3/d": ("Sm3/d", 1.0),
    "sm3/d": ("Sm3/d", 1.0),
    "kSm3/day": ("Sm3/d", 1e3),
    "kSm3/d": ("Sm3/d", 1e3),
    "MSm3/day": ("Sm3/d", 1e6),
    "MSm3/d": ("Sm3/d", 1e6),
    "Sm3": ("Sm3", 1.0),
    "kSm3": ("Sm3", 1e3),
    "MSm3": ("Sm3", 1e6),
    "GSm3": ("Sm3", 1e9),
    "BARa": ("bara", 1.0),
    "bara": ("bara", 1.0),
    "bar": ("bar", 1.0),
    "BARg": ("barg", 1.0),
    "deg C": ("degC", 1.0),
    "degC": ("degC", 1.0),
    "percent": ("%", 1.0),
    "%": ("%", 1.0),
    "fraction": ("%", 100.0),
    "Sm3/Sm3": ("Sm3/Sm3", 1.0),
    "kg/s": ("kg/s", 1.0),
    "m/s": ("m/s", 1.0),
    "MW": ("MW", 1.0),
}

BBL_PER_SM3 = 1 / 0.1589873
