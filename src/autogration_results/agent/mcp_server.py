"""MCP server exposing the view-designer tools.

    uv run --extra agent python -m autogration_results.agent.mcp_server --catalog catalog.json

The server only ever loads the catalog JSON and dashboard YAML; it has no route to results.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from autogration_results.agent.tools import ViewDesigner


def build(designer: ViewDesigner) -> FastMCP:
    mcp = FastMCP("autogration-results-views")

    @mcp.tool()
    def describe_catalog() -> dict[str, Any]:
        """Data-free description of the study: properties, units, dimensions, measures, visual types, health checks."""
        return designer.describe_catalog()

    @mcp.tool()
    def list_measures(available_only: bool = True) -> list[dict[str, Any]]:
        """Measures that can be plotted for this study."""
        return designer.list_measures(available_only)

    @mcp.tool()
    def list_visual_types() -> dict[str, str]:
        """Visual types a dashboard spec may use."""
        return designer.list_visual_types()

    @mcp.tool()
    def list_dashboards() -> list[dict[str, str]]:
        """Existing homepages and custom dashboards."""
        return designer.list_dashboards()

    @mcp.tool()
    def get_dashboard(dashboard_id: str) -> dict[str, Any]:
        """The YAML spec of one dashboard, as a dict."""
        return designer.get_dashboard(dashboard_id)

    @mcp.tool()
    def validate_dashboard(spec: dict[str, Any]) -> dict[str, Any]:
        """Validate a dashboard spec against the schema and this study's catalog."""
        return designer.validate_dashboard(spec)

    @mcp.tool()
    def save_dashboard(spec: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
        """Save a validated spec as a custom dashboard."""
        return designer.save_dashboard(spec, overwrite)

    return mcp


def main() -> None:
    from autogration_results import workbench

    p = argparse.ArgumentParser()
    p.add_argument("--catalog", type=Path, required=True)
    args = p.parse_args()
    designer = ViewDesigner.from_files(args.catalog, workbench.dashboard_dirs(), workbench.custom_dashboard_dir())
    build(designer).run()


if __name__ == "__main__":
    main()
