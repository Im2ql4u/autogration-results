"""Run health checks: did anything fail, collapse or look suspicious?

Thresholds live in ``config/health.yaml`` so the engineer can tune them without code.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel

from autogration_results.contract import COMPLETED, SENTINEL_ABS, UNIT_TO_SI
from autogration_results.engine import ResultStore

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
COLUMNS = ["severity", "check", "case", "run_number", "identifier", "property", "first_timestamp", "count", "detail"]

CHECKS: dict[str, str] = {
    "failed_case": "Case did not complete, so it has no results.",
    "early_termination": "Case results end before the rest of the study.",
    "collapse": "Rate falls below a fraction of its recent average and stays there (shut-in, constraint, non-convergence).",
    "sentinel": "GAP/RESOLVE 'no value' sentinel (3.4e+35) in results; treated as missing.",
    "negative_rate": "A rate property is negative.",
    "missing_value": "Null values in results.",
    "unknown_unit": "Unit not in the unit catalog; values are used unconverted.",
}


class HealthConfig(BaseModel):
    collapse_properties: list[str] = ["AvgLiqRate", "AvgGasRate", "AvgWatInjRate"]
    collapse_identifier_type: str = "WELL"
    collapse_floor_fraction: float = 0.05
    collapse_lookback_months: int = 6
    collapse_min_months: int = 3
    collapse_min_rate: float = 50.0
    rate_property_pattern: str = "%Rate%"

    @classmethod
    def load(cls, path: Path | None) -> HealthConfig:
        if path and path.exists():
            return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        return cls()


def run_checks(store: ResultStore, config: HealthConfig | None = None) -> pd.DataFrame:
    cfg = config or HealthConfig()
    con = store.con
    frames: list[pd.DataFrame] = []

    frames.append(
        con.execute(
            f"""SELECT 'critical' AS severity, 'failed_case' AS "check", case_name AS "case", run_number,
                   NULL AS identifier, NULL AS property, NULL::DATE AS first_timestamp, 1 AS count,
                   'status ' || status || coalesce(': ' || description, '') AS detail
            FROM cases WHERE status <> '{COMPLETED}'"""
        ).df()
    )
    frames.append(
        con.execute(
            """WITH ends AS (SELECT run_number, max(timestamp) AS last FROM raw_results GROUP BY 1)
            SELECT 'warning', 'early_termination', c.case_name, e.run_number, NULL, NULL, e.last, 1,
                   'results end ' || e.last || ', study ends ' || (SELECT max(last) FROM ends)
            FROM ends e JOIN cases c USING (run_number)
            WHERE e.last < (SELECT max(last) FROM ends)"""
        ).df()
    )
    props = ", ".join("?" * len(cfg.collapse_properties))
    floor, back, n = float(cfg.collapse_floor_fraction), int(cfg.collapse_lookback_months), int(cfg.collapse_min_months)
    frames.append(
        con.execute(
            f"""WITH s AS (
                SELECT run_number, identifier, property_name, timestamp, value_si AS v,
                       avg(value_si) OVER w_prev AS prev,
                       max(value_si) OVER w_next AS next_max,
                       count(*) OVER w_next AS next_n
                FROM results_si
                WHERE identifier_type = ? AND property_name IN ({props})
                WINDOW w_prev AS (PARTITION BY run_number, identifier, property_name ORDER BY timestamp
                                  ROWS BETWEEN {back} PRECEDING AND 1 PRECEDING),
                       w_next AS (PARTITION BY run_number, identifier, property_name ORDER BY timestamp
                                  ROWS BETWEEN CURRENT ROW AND {n - 1} FOLLOWING)
            )
            SELECT 'critical', 'collapse', c.case_name, s.run_number, s.identifier, s.property_name,
                   min(s.timestamp), count(*),
                   'drops below {floor:.0%} of its {back}-month average of ' || round(arg_min(s.prev, s.timestamp), 1)
            FROM s JOIN cases c USING (run_number)
            WHERE s.prev > ? AND s.next_n >= {n} AND s.next_max < {floor} * s.prev
            GROUP BY ALL""",
            [cfg.collapse_identifier_type, *cfg.collapse_properties, cfg.collapse_min_rate],
        ).df()
    )
    frames.append(
        con.execute(
            f"""SELECT 'warning', 'sentinel', c.case_name, r.run_number, r.identifier, r.property_name,
                   min(r.timestamp), count(*), 'value >= 1e35 treated as missing'
            FROM raw_results r JOIN cases c USING (run_number)
            WHERE abs(r.value) >= {SENTINEL_ABS} GROUP BY ALL"""
        ).df()
    )
    frames.append(
        con.execute(
            """SELECT 'warning', 'negative_rate', c.case_name, r.run_number, r.identifier, r.property_name,
                   min(r.timestamp), count(*), 'min ' || round(min(r.value), 3)
            FROM raw_results r JOIN cases c USING (run_number)
            WHERE r.property_name LIKE ? AND r.value < 0 GROUP BY ALL""",
            [cfg.rate_property_pattern],
        ).df()
    )
    frames.append(
        con.execute(
            """SELECT 'info', 'missing_value', c.case_name, r.run_number, r.identifier, r.property_name,
                   min(r.timestamp), count(*), 'null values'
            FROM raw_results r JOIN cases c USING (run_number)
            WHERE r.value IS NULL GROUP BY ALL"""
        ).df()
    )
    known = list(UNIT_TO_SI)
    frames.append(
        con.execute(
            f"""SELECT 'info', 'unknown_unit', NULL, NULL, NULL, string_agg(DISTINCT property_name, ', '), NULL::DATE, count(*),
                   'unit ' || unit
            FROM raw_results WHERE unit IS NOT NULL AND unit NOT IN ({', '.join('?' * len(known))})
            GROUP BY unit""",
            known,
        ).df()
    )

    out = pd.concat([f.set_axis(COLUMNS, axis=1) for f in frames if not f.empty], ignore_index=True)
    if out.empty:
        return pd.DataFrame(columns=COLUMNS)
    out["_o"] = out.severity.map(SEVERITY_ORDER)
    return out.sort_values(["_o", "check", "run_number", "identifier"]).drop(columns="_o").reset_index(drop=True)
