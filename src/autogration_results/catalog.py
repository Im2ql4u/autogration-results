"""Data-free catalog: everything the agent may know about a study.

It lists names, units and structure only - properties, dimensions, measures, visual types,
dashboards and health checks. It never contains result values, case names, entity names or
dates. This is the agent's entire view of the data.
"""

from __future__ import annotations

from typing import Any

from autogration_results import __version__
from autogration_results.contract import RESULT_COLUMNS
from autogration_results.engine import GRAIN_SQL, ResultStore
from autogration_results.health import CHECKS
from autogration_results.spec import VISUAL_TYPES, Dashboard

CATALOG_VERSION = 1


def build_catalog(store: ResultStore, dashboards: list[Dashboard] = ()) -> dict[str, Any]:
    props = store.properties()
    measures = []
    for m in store.registry.measures.values():
        ok, reason = store.availability(m)
        measures.append(
            {
                "id": m.id,
                "label": m.label,
                "unit": m.unit,
                "kind": m.kind,
                "identifier_type": m.identifier_type,
                "description": m.description.strip(),
                "higher_is_better": m.higher_is_better,
                "placeholder": m.placeholder,
                "available": ok,
                "reason": reason,
            }
        )
    return {
        "catalog_version": CATALOG_VERSION,
        "package_version": __version__,
        "result_columns": list(RESULT_COLUMNS),
        "properties": [
            {"name": r.property_name, "unit": r.unit_si, "identifier_types": list(r.identifier_types)}
            for r in props.itertuples()
        ],
        "identifier_types": sorted({t for ts in props.identifier_types for t in ts}),
        "dimensions": store.dimensions(),
        "entity_attributes": store.entity_attributes,
        "entity_factors": store.entity_factors,
        "assumption_axes": store.case_axes,
        "grains": list(GRAIN_SQL),
        "case_scopes": {
            "focus": "only the focus case chosen in the filter bar",
            "pair": "focus and baseline case (for comparisons)",
            "selected": "cases selected in the filter bar",
            "all": "all completed cases",
        },
        "measures": measures,
        "visual_types": VISUAL_TYPES,
        "health_checks": CHECKS,
        "dashboards": [{"id": d.id, "title": d.title, "question": d.question} for d in dashboards],
        "counts": {
            "cases": int(store.con.execute("SELECT count(*) FROM cases").fetchone()[0]),
            "entities": int(store.con.execute("SELECT count(*) FROM entities").fetchone()[0]),
        },
    }
