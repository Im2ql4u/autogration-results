"""Tools for the view-designer agent.

Hard rule: this module works from the data-free catalog (a JSON file written by trusted code)
and dashboard specs only. It must never import the engine, renderer, exports, health checks or
data sources - ``tests/test_agent_isolation.py`` enforces that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from autogration_results.spec import VISUAL_TYPES, Dashboard, validate_against_catalog

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class ViewDesigner:
    def __init__(self, catalog: dict[str, Any], dashboard_dirs: list[Path], custom_dir: Path):
        self.catalog = catalog
        self.dashboard_dirs = dashboard_dirs
        self.custom_dir = custom_dir

    @classmethod
    def from_files(cls, catalog_path: Path, dashboard_dirs: list[Path], custom_dir: Path) -> ViewDesigner:
        return cls(json.loads(catalog_path.read_text(encoding="utf-8")), dashboard_dirs, custom_dir)

    def _files(self) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for d in self.dashboard_dirs:
            for f in sorted(d.glob("*.yaml")) if d.exists() else []:
                files[yaml.safe_load(f.read_text(encoding="utf-8"))["id"]] = f
        return files

    def describe_catalog(self) -> dict[str, Any]:
        """Properties, units, dimensions, measures, visual types and health checks. No data."""
        return self.catalog

    def list_measures(self, available_only: bool = True) -> list[dict[str, Any]]:
        return [m for m in self.catalog["measures"] if m["available"] or not available_only]

    def list_visual_types(self) -> dict[str, str]:
        return VISUAL_TYPES

    def list_dashboards(self) -> list[dict[str, str]]:
        out = []
        for board_id, f in self._files().items():
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
            out.append({"id": board_id, "title": raw.get("title", ""), "question": raw.get("question", ""), "custom": f.parent == self.custom_dir})
        return out

    def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        files = self._files()
        if dashboard_id not in files:
            raise KeyError(f"unknown dashboard {dashboard_id!r}")
        return yaml.safe_load(files[dashboard_id].read_text(encoding="utf-8"))

    def validate_dashboard(self, spec: dict[str, Any]) -> dict[str, Any]:
        try:
            board = Dashboard.model_validate(spec)
        except ValidationError as exc:
            return {"ok": False, "errors": [e["msg"] + f" at {'.'.join(map(str, e['loc']))}" for e in exc.errors()], "warnings": []}
        result = validate_against_catalog(board, self.catalog)
        return {"ok": not result["errors"], **result}

    def save_dashboard(self, spec: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
        """Save a validated spec into the custom dashboard folder (never over a built-in one)."""
        check = self.validate_dashboard(spec)
        if not check["ok"]:
            return check
        board = Dashboard.model_validate(spec)
        if not _ID.match(board.id):
            return {"ok": False, "errors": ["id must be lowercase letters, digits, - or _"], "warnings": []}
        existing = self._files().get(board.id)
        if existing and existing.parent != self.custom_dir:
            return {"ok": False, "errors": [f"{board.id} is a built-in dashboard; use a new id"], "warnings": []}
        path = self.custom_dir / f"{board.id}.yaml"
        if path.exists() and not overwrite:
            return {"ok": False, "errors": [f"{board.id} exists; pass overwrite=true"], "warnings": []}
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(board.to_yaml(), encoding="utf-8")
        return {"ok": True, "path": str(path), "warnings": check["warnings"]}
