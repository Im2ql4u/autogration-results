from autogration_results import workbench
from autogration_results.export_specs import (
    build_specs,
    check_web_support,
    render_module,
)
from autogration_results.measures import MeasureRegistry
from autogration_results.spec import Dashboard


def test_published_specs_export_for_the_web_app():
    specs = build_specs()
    assert [d["id"] for d in specs["dashboards"]] == ["overview", "health", "compare", "uncertainty", "wells", "facilities", "data"]
    oil = next(m for m in specs["measures"] if m["id"] == "oil_rate")
    assert oil == {
        "id": "oil_rate",
        "label": "Oil rate",
        "unit": "Sm3/d",
        "kind": "rate",
        "terms": [{"property": "AvgOilRate"}],
        "source": "Calculations.AvgOilRate_Sm3d",
        "identifierType": "WELL",
        "higherIsBetter": True,
    }
    area = specs["dashboards"][0]["sections"][1]["visuals"][0]
    assert area["color"] == ["entity.field", "entity.group", "identifier"] and area["topN"] == 8
    assert specs["health"]["collapseFloorFraction"] == 0.05
    module = render_module(specs, "abc123")
    assert "export const RESULT_SPECS" in module and "abc123" in module


def test_rejects_what_the_web_app_cannot_show():
    board = Dashboard.model_validate(
        {
            "id": "x",
            "title": "X",
            "question": "?",
            "sections": [
                {
                    "title": "s",
                    "visuals": [
                        {"id": "a", "type": "kpi", "measures": ["oil_rate"], "stat": "mean"},
                        {"id": "b", "type": "line", "measure": "nope", "color": "case.status"},
                    ],
                }
            ],
        }
    )
    problems = check_web_support([board], MeasureRegistry.load(workbench.measures_dir()))
    assert any("stat 'mean'" in p for p in problems)
    assert any("unknown measure 'nope'" in p for p in problems)
    assert any("case.status" in p for p in problems)
