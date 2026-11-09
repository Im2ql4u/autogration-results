"""Measure definitions: how a result property becomes a number on a chart.

Measures are declared in YAML (see ``measures/*.yaml``) so engineers can add or tune them
without touching query code. The ``kind`` fixes the aggregation rules, which is what keeps
ratios weighted, rates calendar-day averaged and cumulatives taken at the period end.

kind        across entities          across time (within a period)
rate        sum                      calendar-day weighted mean
volume      sum of rates             sum of rate x days        (terms carry the unit scale)
cumulative  sum                      value at the last timestep
ratio       sum(num) / sum(den)      days-weighted sum(num) / sum(den)
state       mean (or weighted mean)  days-weighted mean
count       entities with value > 0  max
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Kind = Literal["rate", "volume", "cumulative", "ratio", "state", "count"]


class Term(BaseModel):
    property: str
    scale: float = 1.0
    entity_factor: str | None = Field(
        default=None, description="Column in entities.csv multiplied into this term (default 1)."
    )
    si_unit: str | None = Field(default=None, description="Expected SI unit of the property, checked against data.")


class Measure(BaseModel):
    id: str
    label: str
    unit: str
    kind: Kind
    terms: list[Term]
    denominator: list[Term] = []
    weight: list[Term] = []
    identifier_type: str | None = "WELL"
    description: str = ""
    higher_is_better: bool | None = None
    source: str | None = Field(default=None, description="Origin, e.g. the Power BI measure it replaces.")
    placeholder: bool = False

    @model_validator(mode="after")
    def _check(self) -> Measure:
        if self.kind == "ratio" and not self.denominator:
            raise ValueError(f"measure {self.id}: ratio needs a denominator")
        if self.kind != "ratio" and self.denominator:
            raise ValueError(f"measure {self.id}: only ratio measures take a denominator")
        if self.weight and self.kind != "state":
            raise ValueError(f"measure {self.id}: only state measures take a weight")
        return self

    def properties(self) -> set[str]:
        return {t.property for t in [*self.terms, *self.denominator, *self.weight]}

    def entity_factors(self) -> set[str]:
        return {t.entity_factor for t in [*self.terms, *self.denominator, *self.weight] if t.entity_factor}


class MeasureRegistry(BaseModel):
    measures: dict[str, Measure]

    @classmethod
    def load(cls, *paths: Path) -> MeasureRegistry:
        measures: dict[str, Measure] = {}
        for path in paths:
            files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
            for file in files:
                for raw in yaml.safe_load(file.read_text(encoding="utf-8"))["measures"]:
                    m = Measure.model_validate(raw)
                    if m.id in measures:
                        raise ValueError(f"duplicate measure id {m.id} in {file}")
                    measures[m.id] = m
        return cls(measures=measures)

    def __getitem__(self, measure_id: str) -> Measure:
        try:
            return self.measures[measure_id]
        except KeyError:
            raise KeyError(f"unknown measure {measure_id!r}") from None

    def __contains__(self, measure_id: object) -> bool:
        return measure_id in self.measures
