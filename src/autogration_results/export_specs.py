"""Export the specs (measures, dashboards, health thresholds) for the Autogration web app.

The web app does not read YAML. This writes one generated TypeScript module that the frontend
imports (`frontend/src/pages/results/engine/specs.gen.ts`), after checking that every spec only
uses what the web app can show.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from autogration_results import workbench
from autogration_results.measures import Measure, MeasureRegistry
from autogration_results.spec import Dashboard, Visual, load_dashboards

FRONTEND_TARGET = Path("frontend/src/pages/results/engine/specs.gen.ts")

# What the web app supports today; the Python workbench supports more.
WEB_STATS = {"total", "max"}
WEB_GRAINS = {"month", "quarter", "year"}
WEB_DIMS = {"period", "case", "identifier", "system", "identifier_type"}
WEB_DIM_PREFIXES = ("entity.", "case.")
WEB_UNSUPPORTED_DIMS = {"case.status"}


class SpecError(ValueError):
    pass


def _web_dim(dim: str) -> bool:
    return dim not in WEB_UNSUPPORTED_DIMS and (dim in WEB_DIMS or dim.startswith(WEB_DIM_PREFIXES))


def check_web_support(boards: list[Dashboard], registry: MeasureRegistry) -> list[str]:
    problems: list[str] = []
    for m in registry.measures.values():
        if m.weight:
            problems.append(f"measure {m.id}: `weight` is not supported by the web app")
    seen: set[str] = set()
    for b in boards:
        if b.id in seen:
            problems.append(f"dashboard id {b.id} is used twice")
        seen.add(b.id)
        for v in b.visuals():
            where = f"{b.id}/{v.id}"
            for mid in v.measure_ids():
                if mid not in registry:
                    problems.append(f"{where}: unknown measure {mid!r}")
            if v.stat not in WEB_STATS:
                problems.append(f"{where}: stat {v.stat!r} is not supported by the web app (use total or max)")
            if v.grain not in WEB_GRAINS:
                problems.append(f"{where}: grain {v.grain!r} is not supported by the web app")
            if v.sort != "none" or v.filters or v.quantiles != [0.1, 0.5, 0.9]:
                problems.append(f"{where}: sort, filters and quantiles are workbench-only")
            for dim in v.dimensions():
                if not _web_dim(dim):
                    problems.append(f"{where}: dimension {dim!r} is not available in the web app")
    return problems


def _drop_empty(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {}, False)}


def _term(t: Any) -> dict[str, Any]:
    return _drop_empty({"property": t.property, "scale": None if t.scale == 1.0 else t.scale, "entityFactor": t.entity_factor})


def measure_json(m: Measure) -> dict[str, Any]:
    out = _drop_empty(
        {
            "id": m.id,
            "label": m.label,
            "unit": m.unit,
            "kind": m.kind,
            "terms": [_term(t) for t in m.terms],
            "denominator": [_term(t) for t in m.denominator],
            "description": m.description.strip(),
            "placeholder": m.placeholder,
            "source": m.source,
        }
    )
    # null means "every identifier type", so it is kept even when empty.
    out["identifierType"] = m.identifier_type
    if m.higher_is_better is not None:
        out["higherIsBetter"] = m.higher_is_better
    return out


def visual_json(v: Visual) -> dict[str, Any]:
    return _drop_empty(
        {
            "id": v.id,
            "type": v.type,
            "title": v.title,
            "measure": v.measure,
            "measures": v.measures,
            "color": v.color,
            "x": None if v.x == "period" else v.x,
            "rows": v.rows,
            "grain": None if v.grain == "month" else v.grain,
            "cases": None if v.cases == "selected" else v.cases,
            "compare": None if v.compare == "none" else v.compare,
            "stat": None if v.stat == "total" else v.stat,
            "topN": v.top_n,
            "width": None if v.width == 12 else v.width,
            "note": (v.note or "").strip(),
        }
    )


def dashboard_json(b: Dashboard) -> dict[str, Any]:
    return _drop_empty(
        {
            "id": b.id,
            "title": b.title,
            "question": b.question,
            "description": b.description.strip(),
            "sections": [{"title": s.title, "visuals": [visual_json(v) for v in s.visuals]} for s in b.sections],
        }
    )


def health_json(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keys = {
        "collapse_properties": "collapseProperties",
        "collapse_identifier_type": "collapseIdentifierType",
        "collapse_floor_fraction": "collapseFloorFraction",
        "collapse_lookback_months": "collapseLookbackMonths",
        "collapse_min_months": "collapseMinMonths",
        "collapse_min_rate": "collapseMinRate",
    }
    missing = [k for k in keys if k not in raw]
    if missing:
        raise SpecError(f"{path}: missing {', '.join(missing)}")
    return {camel: raw[snake] for snake, camel in keys.items()}


def _revision(root: Path) -> str:
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "specs"], capture_output=True, text=True, check=True).stdout.strip()
        return f"{sha}{' + uncommitted spec changes' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown revision"


def build_specs() -> dict[str, Any]:
    registry = MeasureRegistry.load(workbench.measures_dir())
    boards = load_dashboards(workbench.published_dashboard_dir())
    problems = check_web_support(boards, registry)
    if problems:
        raise SpecError("specs use features the web app cannot show:\n  " + "\n  ".join(problems))
    return {
        "measures": [measure_json(m) for m in registry.measures.values()],
        "dashboards": [dashboard_json(b) for b in boards],
        "health": health_json(workbench.health_config_path()),
    }


def render_module(specs: dict[str, Any], revision: str) -> str:
    body = json.dumps(specs, indent=2, ensure_ascii=False)
    return (
        f"// Generated by `autogration-results export-specs` from autogration-results {revision}.\n"
        "// Do not edit: change specs/ in the autogration-results repo and export again.\n"
        'import type { Dashboard } from "./dashboards"\n'
        'import type { HealthConfig } from "./health"\n'
        'import type { Measure } from "./measures"\n'
        "\n"
        "export const RESULT_SPECS: {\n"
        "  measures: Measure[]\n"
        "  dashboards: Dashboard[]\n"
        "  health: HealthConfig\n"
        f"}} = {body}\n"
    )


def export_specs(out: Path) -> Path:
    module = render_module(build_specs(), _revision(workbench.root()))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(module, encoding="utf-8", newline="\n")
    return out
