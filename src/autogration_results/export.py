"""Exports: tables (CSV/XLSX/Parquet), whole dashboards (XLSX, standalone HTML) and bundles."""

from __future__ import annotations

import datetime as dt
import html
import io
import json
import zipfile

import pandas as pd

from autogration_results import __version__
from autogration_results.render import Rendered, Renderer
from autogration_results.spec import Dashboard


def table_bytes(df: pd.DataFrame, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "csv":
        return df.to_csv(index=False).encode("utf-8-sig")
    if fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
            df.to_excel(xw, index=False, sheet_name="data")
    elif fmt == "parquet":
        df.to_parquet(buf, index=False)
    else:
        raise ValueError(f"unknown format {fmt}")
    return buf.getvalue()


def _provenance(renderer: Renderer, board: Dashboard) -> dict[str, str]:
    return {
        "dashboard": f"{board.title} ({board.id})",
        "question": board.question,
        "source": renderer.store.source,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "package_version": __version__,
        "filters": json.dumps(renderer.ctx.model_dump(mode="json", exclude_none=True)),
    }


def dashboard_xlsx(renderer: Renderer, board: Dashboard, rendered: list[tuple[str, list[Rendered]]] | None = None) -> bytes:
    rendered = rendered or renderer.render_dashboard(board)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        about = pd.DataFrame(list(_provenance(renderer, board).items()), columns=["field", "value"])
        about.to_excel(xw, index=False, sheet_name="About")
        index_rows = []
        used: set[str] = {"About", "Spec"}
        for section, items in rendered:
            for r in items:
                if r.data is None or r.data.empty:
                    continue
                name = r.visual.id[:31]
                i = 2
                while name in used:
                    name = f"{r.visual.id[:28]}_{i}"
                    i += 1
                used.add(name)
                data = r.data.copy()
                for col in data.select_dtypes(include=["datetimetz"]).columns:
                    data[col] = data[col].dt.tz_localize(None)
                data.to_excel(xw, index=False, sheet_name=name, startrow=1)
                xw.sheets[name].write(0, 0, f"{section} / {r.title}")
                index_rows.append({"sheet": name, "section": section, "visual": r.title, "type": r.visual.type})
        pd.DataFrame(index_rows).to_excel(xw, index=False, sheet_name="About", startrow=len(about) + 2)
        pd.DataFrame({"spec_yaml": board.to_yaml().splitlines()}).to_excel(xw, index=False, sheet_name="Spec")
    return buf.getvalue()


def _kpi_html(cards: list[dict]) -> str:
    out = []
    for c in cards:
        value = c.get("value")
        txt = "n/a" if value is None else f"{value:,.4g}"
        delta = ""
        if c.get("delta") is not None:
            color = {True: "#2ca02c", False: "#d62728"}.get(c.get("better"), "#555")
            delta = f'<div style="color:{color}">{c["delta"]:+,.4g} vs baseline</div>'
        out.append(
            f'<div class="kpi"><div class="lbl">{html.escape(str(c["label"]))}</div>'
            f'<div class="val">{html.escape(txt)} <small>{html.escape(str(c.get("unit", "")))}</small></div>{delta}</div>'
        )
    return '<div class="kpis">' + "".join(out) + "</div>"


def dashboard_html(renderer: Renderer, board: Dashboard, rendered: list[tuple[str, list[Rendered]]] | None = None) -> bytes:
    """Standalone HTML report (Plotly JS inlined once, works offline)."""
    rendered = rendered or renderer.render_dashboard(board)
    prov = _provenance(renderer, board)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{html.escape(board.title)}</title>",
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}"
        ".kpis{display:flex;gap:12px;flex-wrap:wrap}.kpi{border:1px solid #ddd;border-radius:6px;padding:10px 14px;min-width:160px}"
        ".lbl{font-size:12px;color:#666}.val{font-size:22px;font-weight:600}table{border-collapse:collapse;font-size:12px}"
        "td,th{border:1px solid #ddd;padding:3px 6px}.meta{font-size:12px;color:#666}.note{white-space:pre-wrap;background:#f6f6f6;padding:8px}</style>",
        "</head><body>",
        f"<h1>{html.escape(board.title)}</h1><p>{html.escape(board.question)}</p>",
        "<p class='meta'>" + "<br>".join(f"{html.escape(k)}: {html.escape(v)}" for k, v in prov.items()) + "</p>",
    ]
    plotly_included = False
    for section, items in rendered:
        parts.append(f"<h2>{html.escape(section)}</h2>")
        for r in items:
            if r.title:
                parts.append(f"<h3>{html.escape(r.title)}</h3>")
            if r.kind == "figure" and r.figure is not None:
                parts.append(r.figure.to_html(full_html=False, include_plotlyjs="inline" if not plotly_included else False))
                plotly_included = True
            elif r.kind == "kpis":
                parts.append(_kpi_html(r.kpis))
            elif r.kind == "table" and r.table is not None:
                parts.append(r.table.to_html(index=False, float_format=lambda x: f"{x:,.4g}"))
            if r.text:
                parts.append(f"<div class='note'>{html.escape(r.text)}</div>")
    parts.append("</body></html>")
    return "\n".join(parts).encode("utf-8")


def dashboard_bundle(renderer: Renderer, board: Dashboard) -> bytes:
    """Zip with XLSX, HTML, spec YAML and one CSV per visual."""
    rendered = renderer.render_dashboard(board)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{board.id}.xlsx", dashboard_xlsx(renderer, board, rendered))
        z.writestr(f"{board.id}.html", dashboard_html(renderer, board, rendered))
        z.writestr(f"{board.id}.yaml", board.to_yaml())
        z.writestr("provenance.json", json.dumps(_provenance(renderer, board), indent=2))
        for _, items in rendered:
            for r in items:
                if r.data is not None and not r.data.empty:
                    z.writestr(f"data/{r.visual.id}.csv", table_bytes(r.data, "csv"))
    return buf.getvalue()


def figure_png(fig, scale: float = 2.0) -> bytes:
    """Needs the optional `png` extra (kaleido)."""
    return fig.to_image(format="png", scale=scale)
