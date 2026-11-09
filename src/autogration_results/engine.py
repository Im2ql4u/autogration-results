"""DuckDB-backed result store and the measure query compiler.

Only trusted code (viewer, backend, exports) touches a ResultStore. The agent gets the
data-free catalog from ``catalog.build_catalog`` instead.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from pathlib import Path

import duckdb
import pandas as pd

from autogration_results.contract import (
    CASE_REQUIRED,
    CASE_RESERVED,
    COMPLETED,
    ENTITY_RESERVED,
    RESULT_COLUMNS,
    SENTINEL_ABS,
    UNIT_TO_SI,
)
from autogration_results.measures import Measure, MeasureRegistry, Term

GRAIN_SQL = {
    "timestamp": "r.timestamp",
    "month": "date_trunc('month', r.timestamp)",
    "quarter": "date_trunc('quarter', r.timestamp)",
    "year": "date_trunc('year', r.timestamp)",
}
BASE_DIMS = {
    "case": "c.case_name",
    "run_number": "r.run_number",
    "identifier": "r.identifier",
    "identifier_type": "r.identifier_type",
    "system": "r.system",
    "module": "r.module",
}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class Unavailable(LookupError):
    """A measure or dimension this data cannot provide (missing property or reference column)."""


class ResultStore:
    def __init__(self, con: duckdb.DuckDBPyConnection, registry: MeasureRegistry, source: str) -> None:
        self.con = con
        self.registry = registry
        self.source = source
        self._prepare()

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_frames(
        cls,
        results: pd.DataFrame,
        registry: MeasureRegistry,
        cases: pd.DataFrame | None = None,
        entities: pd.DataFrame | None = None,
        source: str = "in-memory",
    ) -> ResultStore:
        con = duckdb.connect()
        con.register("_results_in", results)
        cols = ", ".join(f"CAST({_q(c)} AS {t}) AS {_q(c)}" for c, t in RESULT_COLUMNS.items())
        con.execute(f"CREATE TABLE raw_results AS SELECT {cols} FROM _results_in")
        con.unregister("_results_in")
        return cls._finish(con, registry, cases, entities, source)

    @classmethod
    def from_folder(cls, folder: Path, registry: MeasureRegistry) -> ResultStore:
        """Folder layout: results/*.parquet (Autogration task Parquet), optional cases.csv, entities.csv."""
        files = sorted((folder / "results").glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"no Parquet files in {folder / 'results'}")
        con = duckdb.connect()
        cols = ", ".join(f"CAST({_q(c)} AS {t}) AS {_q(c)}" for c, t in RESULT_COLUMNS.items() if c != "run_number")
        con.execute(
            f"CREATE TABLE raw_results AS SELECT {cols}, CAST(coalesce(run_number, 1) AS INTEGER) AS run_number "
            "FROM read_parquet(?, union_by_name = true)",
            [[str(f) for f in files]],
        )
        cases = pd.read_csv(folder / "cases.csv") if (folder / "cases.csv").exists() else None
        entities = pd.read_csv(folder / "entities.csv") if (folder / "entities.csv").exists() else None
        return cls._finish(con, registry, cases, entities, str(folder))

    @classmethod
    def _finish(cls, con, registry, cases, entities, source) -> ResultStore:
        con.register("_cases_in", cases if cases is not None else pd.DataFrame({"run_number": []}))
        con.register("_entities_in", entities if entities is not None else pd.DataFrame({"identifier": []}))
        con.execute("CREATE TABLE cases_in AS SELECT * FROM _cases_in")
        con.execute("CREATE TABLE entities_in AS SELECT * FROM _entities_in")
        con.unregister("_cases_in")
        con.unregister("_entities_in")
        return cls(con, registry, source)

    def _prepare(self) -> None:
        con = self.con
        units = pd.DataFrame([(u, si, f) for u, (si, f) in UNIT_TO_SI.items()], columns=["unit", "si_unit", "factor"])
        con.register("_units", units)
        con.execute("CREATE OR REPLACE TABLE units AS SELECT * FROM _units")
        con.unregister("_units")
        con.execute(
            f"""CREATE OR REPLACE VIEW results_si AS
            SELECT r.*, coalesce(u.si_unit, r.unit) AS unit_si,
                   CASE WHEN abs(r.value) >= {SENTINEL_ABS} THEN NULL ELSE r.value * coalesce(u.factor, 1) END AS value_si
            FROM raw_results r LEFT JOIN units u ON r.unit = u.unit"""
        )
        self._prepare_cases()
        self._prepare_entities()

    def _prepare_cases(self) -> None:
        con = self.con
        have = {r[0] for r in con.execute("DESCRIBE cases_in").fetchall()}
        runs = con.execute("SELECT DISTINCT run_number FROM raw_results").fetchall()
        if not have >= set(CASE_REQUIRED) or con.execute("SELECT count(*) FROM cases_in").fetchone()[0] == 0:
            con.execute(
                "CREATE OR REPLACE TABLE cases_in AS SELECT run_number, 'Case ' || run_number AS case_name "
                "FROM (SELECT DISTINCT run_number FROM raw_results)"
            )
            have = {"run_number", "case_name"}
        extra = [c for c in have if c not in CASE_RESERVED]
        cols = [
            "CAST(run_number AS INTEGER) AS run_number",
            "CAST(case_name AS VARCHAR) AS case_name",
            "CAST(task_id AS VARCHAR) AS task_id" if "task_id" in have else "NULL::VARCHAR AS task_id",
            f"coalesce(CAST(status AS VARCHAR), '{COMPLETED}') AS status" if "status" in have else f"'{COMPLETED}' AS status",
            "coalesce(CAST(is_baseline AS BOOLEAN), false) AS is_baseline" if "is_baseline" in have else "false AS is_baseline",
            "CAST(description AS VARCHAR) AS description" if "description" in have else "NULL::VARCHAR AS description",
            *[f"CAST({_q(c)} AS VARCHAR) AS {_q(c)}" for c in extra],
        ]
        con.execute(f"CREATE OR REPLACE TABLE cases AS SELECT {', '.join(cols)} FROM cases_in")
        missing = {r[0] for r in runs} - {r[0] for r in con.execute("SELECT run_number FROM cases").fetchall()}
        for run in sorted(missing):
            con.execute("INSERT INTO cases (run_number, case_name, status, is_baseline) VALUES (?, ?, ?, false)", [run, f"Case {run}", COMPLETED])
        if not con.execute("SELECT bool_or(is_baseline) FROM cases").fetchone()[0]:
            con.execute(
                f"UPDATE cases SET is_baseline = true WHERE run_number = "
                f"(SELECT min(run_number) FROM cases WHERE status = '{COMPLETED}')"
            )
        self.case_axes = sorted(extra)

    def _prepare_entities(self) -> None:
        con = self.con
        have = {r[0]: r[1] for r in con.execute("DESCRIBE entities_in").fetchall()}
        extra = [c for c in have if c not in ENTITY_RESERVED]
        type_join = "AND ei.identifier_type = k.identifier_type" if "identifier_type" in have else ""
        cols = ", ".join(f"ei.{_q(c)}" for c in extra)
        con.execute(
            f"""CREATE OR REPLACE TABLE entities AS
            SELECT k.identifier, k.identifier_type{', ' + cols if cols else ''}
            FROM (SELECT DISTINCT identifier, identifier_type FROM raw_results) k
            LEFT JOIN entities_in ei ON CAST(ei.identifier AS VARCHAR) = k.identifier {type_join}"""
        )
        numeric = ("DOUBLE", "FLOAT", "BIGINT", "INTEGER", "DECIMAL", "HUGEINT", "SMALLINT")
        self.entity_attributes = sorted(c for c in extra if not str(have[c]).upper().startswith(numeric))
        self.entity_factors = sorted(c for c in extra if str(have[c]).upper().startswith(numeric))

    # ---------------------------------------------------------- introspection
    def dimensions(self) -> list[str]:
        return [
            "period",
            *BASE_DIMS,
            *[f"entity.{c}" for c in self.entity_attributes],
            "case.status",
            *[f"case.{c}" for c in self.case_axes],
        ]

    def properties(self) -> pd.DataFrame:
        if not hasattr(self, "_properties"):
            self._properties = self.con.execute(
                """SELECT property_name, unit_si, list(DISTINCT identifier_type ORDER BY identifier_type) AS identifier_types
                FROM results_si GROUP BY ALL ORDER BY property_name, unit_si"""
            ).df()
        return self._properties

    def availability(self, m: Measure) -> tuple[bool, str]:
        props = self.properties()
        for t in [*m.terms, *m.denominator, *m.weight]:
            rows = props[props.property_name == t.property]
            if m.identifier_type:
                rows = rows[rows.identifier_types.apply(lambda ts: m.identifier_type in list(ts))]
            if rows.empty:
                where = f" on {m.identifier_type}" if m.identifier_type else ""
                return False, f"property {t.property}{where} not in results"
            if t.si_unit and t.si_unit not in set(rows.unit_si):
                return False, f"{t.property} has unit {sorted(set(rows.unit_si.dropna()))}, expected {t.si_unit}"
        for f in m.entity_factors():
            if f not in self.entity_factors:
                return False, f"entities.csv has no numeric column {f}"
        return True, ""

    def cases(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM cases ORDER BY run_number").df()

    def case_names(self, completed_only: bool = True) -> list[str]:
        where = f"WHERE status = '{COMPLETED}'" if completed_only else ""
        return [r[0] for r in self.con.execute(f"SELECT case_name FROM cases {where} ORDER BY run_number").fetchall()]

    def baseline(self) -> str | None:
        row = self.con.execute("SELECT case_name FROM cases WHERE is_baseline ORDER BY run_number LIMIT 1").fetchone()
        return row[0] if row else None

    def entity_values(self, attribute: str) -> list[str]:
        if attribute not in self.entity_attributes:
            raise KeyError(attribute)
        rows = self.con.execute(f"SELECT DISTINCT CAST({_q(attribute)} AS VARCHAR) FROM entities WHERE {_q(attribute)} IS NOT NULL ORDER BY 1").fetchall()
        return [r[0] for r in rows]

    def date_range(self) -> tuple[dt.date, dt.date]:
        lo, hi = self.con.execute("SELECT min(timestamp), max(timestamp) FROM raw_results").fetchone()
        return lo, hi

    # ----------------------------------------------------------------- queries
    def _dim_sql(self, dim: str) -> str:
        if dim in BASE_DIMS:
            return BASE_DIMS[dim]
        if dim.startswith("entity.") and dim[7:] in self.entity_attributes:
            return f"e.{_q(dim[7:])}"
        if dim == "case.status":
            return "c.status"
        if dim.startswith("case.") and dim[5:] in self.case_axes:
            return f"c.{_q(dim[5:])}"
        if dim.startswith("entity."):
            raise Unavailable(f"dimension {dim}: entities.csv has no column {dim[7:]!r}")
        if dim.startswith("case."):
            raise Unavailable(f"dimension {dim}: cases.csv has no column {dim[5:]!r}")
        raise ValueError(f"unknown dimension {dim!r}")

    def query(
        self,
        measure_id: str,
        dims: Sequence[str] = ("period",),
        *,
        grain: str = "month",
        cases: Iterable[str] | None = None,
        filters: dict[str, list[str]] | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> pd.DataFrame:
        """One measure, grouped by dims. Returns columns ``[*dims, "value"]``.

        Without a case dimension, values are averaged over the selected cases.
        """
        m = self.registry[measure_id]
        ok, reason = self.availability(m)
        if not ok:
            raise Unavailable(f"{measure_id}: {reason}")
        dims = list(dict.fromkeys(dims))
        other = [d for d in dims if d != "period"]
        dim_cols = [f"d{i}" for i in range(len(other))]
        props = sorted(m.properties())
        factors = sorted(m.entity_factors())
        params: list[object] = []

        pivot = []
        for i, p in enumerate(props):
            pivot.append(f"sum(r.value_si) FILTER (WHERE r.property_name = ?) AS p{i}")
            params.append(p)
        period_sql = GRAIN_SQL[grain] if "period" in dims else "DATE '1900-01-01'"
        select_dims = [f"{self._dim_sql(d)} AS {a}" for d, a in zip(other, dim_cols)]
        select_factors = [f"any_value(e.{_q(f)}) AS f{i}" for i, f in enumerate(factors)]

        where = ["r.property_name IN (" + ", ".join("?" * len(props)) + ")", f"c.status = '{COMPLETED}'"]
        params.extend(props)
        if m.identifier_type:
            where.append("r.identifier_type = ?")
            params.append(m.identifier_type)
        if cases is not None:
            cases = list(cases)
            where.append("c.case_name IN (" + (", ".join("?" * len(cases)) or "NULL") + ")")
            params.extend(cases)
        for dim, values in (filters or {}).items():
            if values:
                where.append(f"CAST({self._dim_sql(dim)} AS VARCHAR) IN (" + ", ".join("?" * len(values)) + ")")
                params.extend(str(v) for v in values)
        if date_from:
            where.append("r.timestamp >= ?")
            params.append(date_from)
        if date_to:
            where.append("r.timestamp <= ?")
            params.append(date_to)

        def combine(terms: list[Term]) -> str:
            parts = []
            for t in terms:
                expr = f"{t.scale!r} * p{props.index(t.property)}"
                if t.entity_factor:
                    expr += f" * f{factors.index(t.entity_factor)}"
                parts.append(expr)
            if len(parts) == 1:
                return parts[0]
            all_null = " AND ".join(f"p{props.index(t.property)} IS NULL" for t in terms)
            return f"CASE WHEN {all_null} THEN NULL ELSE " + " + ".join(f"coalesce({p}, 0)" for p in parts) + " END"

        num = combine(m.terms)
        den = combine(m.denominator) if m.denominator else "NULL"
        w = combine(m.weight) if m.weight else "NULL"
        g = ", ".join(["run_number", "timestamp", *dim_cols])

        if m.kind == "ratio":
            s1 = "sum(num) AS s_num, sum(den) AS s_den"
        elif m.kind == "state" and m.weight:
            s1 = "sum(num * w) AS s_num, sum(w) FILTER (WHERE num IS NOT NULL) AS s_den"
        elif m.kind == "state":
            s1 = "sum(num) AS s_num, count(num) AS s_den"
        elif m.kind == "count":
            s1 = "count(DISTINCT identifier) FILTER (WHERE num > 0) AS s_num, NULL AS s_den"
        else:
            s1 = "sum(num) AS s_num, NULL AS s_den"

        v1 = "s_num / nullif(s_den, 0)" if m.kind in ("ratio", "state") else "s_num"
        days = "dayofmonth(last_day(timestamp))"
        s2 = {
            "rate": f"sum(v1 * {days}) / nullif(sum({days}) FILTER (WHERE v1 IS NOT NULL), 0)",
            "volume": f"sum(v1 * {days})",
            "cumulative": "arg_max(v1, timestamp) FILTER (WHERE v1 IS NOT NULL)",
            "ratio": f"sum(s_num * {days}) / nullif(sum(s_den * {days}), 0)",
            "state": f"sum(s_num * {days}) / nullif(sum(s_den * {days}), 0)",
            "count": "max(v1)",
        }[m.kind]
        gd = ", ".join(["period", *dim_cols])

        sql = f"""
        WITH base AS (
            SELECT r.run_number, r.identifier, r.timestamp, {period_sql} AS period
                   {''.join(', ' + s for s in select_dims)}, {', '.join(pivot)}
                   {''.join(', ' + s for s in select_factors)}
            FROM results_si r
            JOIN cases c USING (run_number)
            LEFT JOIN entities e ON e.identifier = r.identifier AND e.identifier_type = r.identifier_type
            WHERE {' AND '.join(where)}
            GROUP BY ALL
        ), rows AS (
            SELECT *, {num} AS num, {den} AS den, {w} AS w FROM base
        ), spatial AS (
            SELECT {g}, any_value(period) AS period, {s1} FROM rows GROUP BY {g}
        ), temporal AS (
            SELECT run_number, {gd}, {s2} AS v2
            FROM (SELECT *, {v1} AS v1 FROM spatial)
            GROUP BY run_number, {gd}
        )
        SELECT {gd}, avg(v2) AS value FROM temporal GROUP BY {gd} ORDER BY {gd}
        """
        df = self.con.execute(sql, params).df()
        df = df.rename(columns=dict(zip(dim_cols, other)))
        if "period" not in dims:
            df = df.drop(columns="period")
        return df[[*dims, "value"]]
