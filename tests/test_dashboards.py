from autogration_results import workbench
from autogration_results.catalog import build_catalog
from autogration_results.export import dashboard_bundle, dashboard_html, dashboard_xlsx
from autogration_results.health import HealthConfig, run_checks
from autogration_results.render import Renderer
from autogration_results.spec import FilterContext, validate_against_catalog


def test_example_has_cases_and_baseline(store):
    cases = store.cases()
    assert len(cases) == 8
    assert store.baseline() == "Base"
    assert (cases.status == "Failed").sum() == 1
    assert set(store.case_axes) == {"sep_pressure", "gas_lift", "infill"}


def test_dashboards_valid_for_example(store, boards):
    catalog = build_catalog(store, boards)
    assert {b.id for b in boards} >= {"overview", "health", "compare", "uncertainty", "wells", "facilities", "data"}
    for b in boards:
        result = validate_against_catalog(b, catalog)
        assert result == {"errors": [], "warnings": []}, (b.id, result)


def test_every_visual_renders(store, boards):
    renderer = Renderer(store, health_config=HealthConfig.load(workbench.health_config_path()))
    for b in boards:
        for section, items in renderer.render_dashboard(b):
            for r in items:
                assert r.kind != "unavailable", (b.id, r.visual.id, r.text)
                if r.kind == "figure":
                    assert r.figure.to_json()
                if r.kind in ("figure", "table") and r.visual.type != "health_table":
                    assert r.data is not None and not r.data.empty, (b.id, r.visual.id)


def test_compare_delta_positive_for_best_case(store, boards):
    compare = next(b for b in boards if b.id == "compare")
    renderer = Renderer(store, FilterContext(focus="Low sep + Gas lift + Infill wells", baseline="Base"))
    kpis = renderer.render(compare.visuals()[0]).kpis
    oe = next(k for k in kpis if k["measure"] == "oe_volume_mmboe")
    assert oe["delta"] > 0 and oe["better"] is True


def test_health_finds_planted_problems(store):
    h = run_checks(store, HealthConfig.load(workbench.health_config_path()))
    found = set(zip(h.check, h.case))
    assert ("failed_case", "Low sep + Gas lift") in found
    assert ("early_termination", "Low sep") in found
    assert ("sentinel", "Gas lift") in found
    assert ("negative_rate", "Gas lift + Infill wells") in found
    collapsed = h[h.check == "collapse"]
    assert set(collapsed.case) == {"Low sep + Infill wells"}
    assert set(collapsed.identifier) == {"C-P1", "C-P2"}


def test_exports(store, boards):
    renderer = Renderer(store)
    board = next(b for b in boards if b.id == "overview")
    assert dashboard_xlsx(renderer, board)[:2] == b"PK"
    assert b"<html>" in dashboard_html(renderer, board)
    assert dashboard_bundle(renderer, board)[:2] == b"PK"
