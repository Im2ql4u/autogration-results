"""Command line: generate the example, write the agent catalog, validate specs, export dashboards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autogration_results import workbench


def _store(args: argparse.Namespace):
    return workbench.open_store(args.source, args.path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autogration-results")
    sub = p.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("example", help="(re)generate the synthetic example study")
    ex.add_argument("--out", type=Path, default=None)

    es = sub.add_parser("export-specs", help="check the specs and write them for the Autogration web app")
    target = es.add_mutually_exclusive_group(required=True)
    target.add_argument("--frontend", type=Path, help="path to an autogration_frontend checkout")
    target.add_argument("--out", type=Path, help="write the generated module to this file instead")

    for name, helptext in (("catalog", "write the data-free agent catalog"), ("check", "validate all dashboards"), ("export", "export a dashboard")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--source", choices=["example", "autogration", "gap"], default="example")
        s.add_argument("--path", type=Path, default=None)
        if name == "catalog":
            s.add_argument("--out", type=Path, default=Path("catalog.json"))
        if name == "export":
            s.add_argument("--dashboard", required=True)
            s.add_argument("--format", choices=["html", "xlsx", "zip"], default="html")
            s.add_argument("--out", type=Path, required=True)

    args = p.parse_args(argv)

    if args.cmd == "example":
        from autogration_results.examples import write_example

        print(write_example(args.out or workbench.example_dir()))
        return 0

    if args.cmd == "export-specs":
        from autogration_results.export_specs import FRONTEND_TARGET, SpecError, export_specs

        out = args.out or args.frontend / FRONTEND_TARGET
        if args.frontend and not out.parent.exists():
            print(f"{args.frontend} does not look like an autogration_frontend checkout ({out.parent} missing)", file=sys.stderr)
            return 2
        try:
            print(export_specs(out))
        except SpecError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0

    from autogration_results.catalog import build_catalog
    from autogration_results.spec import load_dashboards, validate_against_catalog

    store = _store(args)
    boards = load_dashboards(*workbench.dashboard_dirs())
    catalog = build_catalog(store, boards)

    if args.cmd == "catalog":
        args.out.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        print(args.out)
        return 0

    if args.cmd == "check":
        failed = False
        for b in boards:
            r = validate_against_catalog(b, catalog)
            for e in r["errors"]:
                print(f"ERROR {b.id}: {e}")
            for w in r["warnings"]:
                print(f"warn  {b.id}: {w}")
            failed |= bool(r["errors"])
        print("ok" if not failed else "failed")
        return int(failed)

    from autogration_results import export
    from autogration_results.health import HealthConfig
    from autogration_results.render import Renderer

    board = next((b for b in boards if b.id == args.dashboard), None)
    if board is None:
        print(f"unknown dashboard {args.dashboard}", file=sys.stderr)
        return 2
    renderer = Renderer(store, health_config=HealthConfig.load(workbench.health_config_path()))
    data = {"html": export.dashboard_html, "xlsx": export.dashboard_xlsx, "zip": export.dashboard_bundle}[args.format](renderer, board)
    args.out.write_bytes(data)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
