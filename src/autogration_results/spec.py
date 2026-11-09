"""Dashboard view specs: the contract shared by the engineer, the web app and the agent.

A dashboard (homepage) answers one question. It has sections; each section has visuals laid
out on a 12-column grid. A visual names measures and dimensions only - never data.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

SPEC_VERSION = 1

VisualType = Literal[
    "kpi",
    "line",
    "area",
    "column",
    "bar",
    "heatmap",
    "table",
    "waterfall",
    "fan",
    "tornado",
    "s_curve",
    "health_summary",
    "health_matrix",
    "health_table",
    "note",
]

VISUAL_TYPES: dict[str, str] = {
    "kpi": "Cards with one number per measure for the focus case; optional delta vs baseline. Uses `measures`, `stat`.",
    "line": "Measure over `x` (usually period), one line per `color` value.",
    "area": "Stacked area of a measure over `x`, stacked by `color` (contribution to a total).",
    "column": "Vertical bars of a measure per `x`, stacked by `color`.",
    "bar": "Horizontal bars per `x` category, sorted. Good for ranking cases or entities.",
    "heatmap": "Measure as colour over `x` (period) and `color` (category rows).",
    "table": "Rows grouped by `rows` dimensions, one column per measure in `measures`.",
    "waterfall": "Incremental (focus - baseline) total of a measure, broken down by `x` category.",
    "fan": "Spread across cases over time: P90/P50/P10 band plus baseline.",
    "tornado": "Range of a measure total across levels of each assumption axis (case.* columns).",
    "s_curve": "Exceedance curve of a measure total across cases with P90/P50/P10 marked.",
    "health_summary": "Counts of health findings by severity and failed cases.",
    "health_matrix": "Health findings per case x check.",
    "health_table": "List of health findings with case, entity, property, first date and detail.",
    "note": "Markdown text: explanations, TODOs for the engineer, links.",
}

CaseScope = Literal["focus", "pair", "selected", "all"]
Grain = Literal["timestamp", "month", "quarter", "year"]
Stat = Literal["total", "max", "min", "last", "mean"]

_NEEDS_MEASURE = {"line", "area", "column", "bar", "heatmap", "waterfall", "fan", "tornado", "s_curve"}

# A list means "the first dimension the data has", e.g. [entity.field, identifier] without reference data.
Dims = str | list[str]


def first_available(dim: Dims | None, available: set[str]) -> str | None:
    if dim is None or isinstance(dim, str):
        return dim
    return next((d for d in dim if d in available), None)


def _flat(dim: Dims | None) -> list[str]:
    if dim is None:
        return []
    return [dim] if isinstance(dim, str) else list(dim)


class Visual(BaseModel):
    id: str
    type: VisualType
    title: str = ""
    note: str | None = None
    measure: str | None = None
    measures: list[str] = []
    x: Dims = "period"
    color: Dims | None = None
    rows: list[str] = []
    grain: Grain = "month"
    cases: CaseScope = "selected"
    compare: Literal["none", "delta"] = "none"
    stat: Stat = "total"
    quantiles: list[float] = [0.1, 0.5, 0.9]
    top_n: int | None = Field(default=None, ge=1, le=200)
    sort: Literal["none", "asc", "desc"] = "none"
    width: int = Field(default=12, ge=1, le=12)
    filters: dict[str, list[str]] = {}

    @model_validator(mode="after")
    def _check(self) -> Visual:
        if self.type in _NEEDS_MEASURE and not self.measure:
            raise ValueError(f"visual {self.id}: type {self.type} needs `measure`")
        if self.type in {"kpi", "table"} and not self.measures:
            raise ValueError(f"visual {self.id}: type {self.type} needs `measures`")
        if self.type == "table" and not self.rows:
            raise ValueError(f"visual {self.id}: table needs `rows`")
        if self.type == "note" and not self.note:
            raise ValueError(f"visual {self.id}: note needs `note` text")
        if self.type == "waterfall" and self.x == "period":
            raise ValueError(f"visual {self.id}: waterfall needs a category `x`, e.g. entity.field")
        return self

    def measure_ids(self) -> list[str]:
        return [m for m in [self.measure, *self.measures] if m]

    def dimensions(self) -> list[str]:
        return [d for d in [*_flat(self.x), *_flat(self.color), *self.rows, *self.filters] if d]


class Section(BaseModel):
    title: str
    description: str = ""
    visuals: list[Visual]


class Dashboard(BaseModel):
    spec_version: int = SPEC_VERSION
    id: str
    title: str
    question: str
    order: int = 100
    description: str = ""
    sections: list[Section]

    @model_validator(mode="after")
    def _unique_ids(self) -> Dashboard:
        ids = [v.id for s in self.sections for v in s.visuals]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"dashboard {self.id}: duplicate visual ids {sorted(dupes)}")
        return self

    def visuals(self) -> list[Visual]:
        return [v for s in self.sections for v in s.visuals]

    @classmethod
    def from_yaml(cls, text: str) -> Dashboard:
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(exclude_defaults=True), sort_keys=False, allow_unicode=True)


def load_dashboards(*dirs: Path) -> list[Dashboard]:
    boards = [Dashboard.from_yaml(f.read_text(encoding="utf-8")) for d in dirs if d.exists() for f in sorted(d.glob("*.yaml"))]
    return sorted(boards, key=lambda b: (b.order, b.title))


class FilterContext(BaseModel):
    """Global filter bar state, applied to every visual."""

    cases: list[str] | None = None
    focus: str | None = None
    baseline: str | None = None
    entity: dict[str, list[str]] = {}
    date_from: dt.date | None = None
    date_to: dt.date | None = None


def validate_against_catalog(board: Dashboard, catalog: dict[str, Any]) -> dict[str, list[str]]:
    """Check a dashboard against the data-free catalog (safe for the agent to call).

    Errors are unknown measures/dimensions. Warnings are measures this data cannot fill;
    those visuals render as "not available" instead of failing.
    """
    errors: list[str] = []
    warnings: list[str] = []
    measures = {m["id"]: m for m in catalog["measures"]}
    dims = set(catalog["dimensions"])
    for v in board.visuals():
        for mid in v.measure_ids():
            if mid not in measures:
                errors.append(f"{v.id}: unknown measure {mid!r}")
            elif not measures[mid]["available"]:
                warnings.append(f"{v.id}: measure {mid!r} not available for this data ({measures[mid]['reason']})")
        choices = [_flat(v.x), _flat(v.color)] + [[d] for d in [*v.rows, *v.filters]]
        for options in choices:
            if not options or any(d in dims for d in options):
                continue
            if all(d.startswith(("entity.", "case.")) for d in options):
                warnings.append(f"{v.id}: {' or '.join(options)} needs reference data (entities.csv / cases.csv column)")
            else:
                errors.append(f"{v.id}: unknown dimension {' or '.join(options)!r}")
    return {"errors": errors, "warnings": warnings}
