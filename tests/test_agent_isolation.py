"""The agent must never see result values, case names, entity names or dates."""

import ast
import json
from pathlib import Path

import autogration_results.agent.tools as tools
from autogration_results.agent.tools import ViewDesigner
from autogration_results.catalog import build_catalog

FORBIDDEN = {"engine", "render", "export", "health", "sources", "examples", "duckdb", "pandas"}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {part for n in names for part in n.split(".")}


def test_agent_modules_do_not_import_data_code():
    agent_dir = Path(tools.__file__).parent
    for f in agent_dir.glob("*.py"):
        assert not (_imports(f) & FORBIDDEN), f


def test_catalog_contains_no_data(store, boards):
    text = json.dumps(build_catalog(store, boards))
    cases = store.cases()
    entities = store.con.execute("SELECT identifier FROM entities").df().identifier
    for name in [*cases.case_name, *cases.task_id.dropna(), *entities]:
        assert f'"{name}"' not in text, name
    lo, hi = store.date_range()
    assert str(lo) not in text and str(hi) not in text
    values = store.con.execute("SELECT DISTINCT round(value, 3) FROM raw_results WHERE abs(value) > 1000 LIMIT 50").fetchall()
    for (v,) in values:
        assert str(v) not in text


def test_designer_validates_and_saves(store, boards, tmp_path):
    catalog = build_catalog(store, boards)
    d = ViewDesigner(catalog, [Path(__file__).parents[1] / "specs" / "dashboards", tmp_path], tmp_path)
    bad = {"id": "x1", "title": "X", "question": "?", "sections": [{"title": "s", "visuals": [{"id": "v", "type": "line", "measure": "nope"}]}]}
    assert d.validate_dashboard(bad)["ok"] is False
    good = {**bad, "sections": [{"title": "s", "visuals": [{"id": "v", "type": "line", "measure": "oil_rate", "color": "case"}]}]}
    saved = d.save_dashboard(good)
    assert saved["ok"] and Path(saved["path"]).exists()
    assert d.save_dashboard({**good, "id": "overview"})["ok"] is False
