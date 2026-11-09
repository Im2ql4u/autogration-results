"""Results workbench viewer.

    uv run --extra app streamlit run app/viewer.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from autogration_results import export, workbench
from autogration_results.agent.tools import ViewDesigner
from autogration_results.catalog import build_catalog
from autogration_results.health import HealthConfig
from autogration_results.render import Rendered, Renderer
from autogration_results.spec import Dashboard, FilterContext, load_dashboards

st.set_page_config(page_title="Autogration results", layout="wide")

SOURCES = {
    "Example study": "example",
    "Autogration export folder": "autogration",
    "GAP CSV folder (Power BI share layout)": "gap",
}


@st.cache_resource(show_spinner="Loading results ...")
def get_store(kind: str, path: str | None):
    return workbench.open_store(kind, Path(path) if path else None)


def fmt(value) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:,.4g}"


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("Results")
    source_label = st.radio("Data source", list(SOURCES), index=0)
    kind = SOURCES[source_label]
    path = None
    if kind != "example":
        path = st.text_input("Folder", help="Autogration: results/*.parquet + cases.csv + entities.csv. GAP: the YGG_PowerBI_share folder.")
        if not path:
            st.stop()
    try:
        store = get_store(kind, path)
    except Exception as exc:  # shown to the user, nothing else to do
        st.error(f"Could not load: {exc}")
        st.stop()

    cases = store.case_names()
    st.subheader("Filters")
    selected = st.multiselect("Cases", cases, default=cases)
    baseline_default = store.baseline()
    baseline = st.selectbox("Baseline case", cases, index=cases.index(baseline_default) if baseline_default in cases else 0)
    focus_options = [c for c in selected if c != baseline] or cases
    focus = st.selectbox("Focus case", focus_options)
    entity_filters = {}
    for attr in [a for a in ("field", "group") if a in store.entity_attributes]:
        values = store.entity_values(attr)
        chosen = st.multiselect(attr.capitalize(), values)
        if chosen:
            entity_filters[f"entity.{attr}"] = chosen
    lo, hi = store.date_range()
    years = st.slider("Years", lo.year, hi.year, (lo.year, hi.year))
    st.caption(f"Source: {store.source}")

ctx = FilterContext(
    cases=selected,
    focus=focus,
    baseline=baseline,
    entity=entity_filters,
    date_from=pd.Timestamp(years[0], 1, 1).date(),
    date_to=pd.Timestamp(years[1], 12, 31).date(),
)
renderer = Renderer(store, ctx, HealthConfig.load(workbench.health_config_path()))
boards = load_dashboards(*workbench.dashboard_dirs())
catalog = build_catalog(store, boards)


# ------------------------------------------------------------------ rendering
def download_buttons(r: Rendered, key: str) -> None:
    if r.data is None or r.data.empty:
        return
    c1, c2 = st.columns(2)
    c1.download_button("CSV", export.table_bytes(r.data, "csv"), f"{r.visual.id}.csv", key=f"{key}-csv")
    c2.download_button("XLSX", export.table_bytes(r.data, "xlsx"), f"{r.visual.id}.xlsx", key=f"{key}-xlsx")


def show(r: Rendered, key: str) -> None:
    if r.title and r.kind != "markdown":
        st.markdown(f"**{r.title}**")
    if r.kind == "figure":
        st.plotly_chart(r.figure, use_container_width=True, key=key)
    elif r.kind == "kpis":
        cols = st.columns(len(r.kpis) or 1)
        for col, card in zip(cols, r.kpis):
            delta = None
            if card.get("delta") is not None:
                pct = card.get("delta_pct")
                delta = f"{card['delta']:+,.4g}" + (f" ({pct:+.1f} %)" if pct is not None else "")
            higher = card.get("better")
            color = "off" if higher is None else ("normal" if (card["delta"] > 0) == higher else "inverse")
            label = card["label"] + (" *" if card.get("placeholder") else "")
            col.metric(f"{label} [{card.get('unit', '')}]", fmt(card.get("value")), delta, delta_color=color, help=card.get("note"))
    elif r.kind == "table":
        st.dataframe(r.table, use_container_width=True, hide_index=True)
    elif r.kind == "markdown":
        st.info(r.text)
        return
    elif r.kind == "unavailable":
        st.warning(f"Not available for this data: {r.text}")
        return
    if r.text and r.kind != "markdown":
        st.caption(r.text)
    if r.kind in ("figure", "table"):
        with st.popover("Data"):
            download_buttons(r, key)


def show_dashboard(board: Dashboard, prefix: str) -> None:
    st.markdown(f"#### {board.question}")
    if board.description:
        st.caption(board.description)
    for si, section in enumerate(board.sections):
        st.subheader(section.title)
        row, used = [], 0
        rows = []
        for v in section.visuals:
            if used + v.width > 12:
                rows.append(row)
                row, used = [], 0
            row.append(v)
            used += v.width
        rows.append(row)
        for ri, row in enumerate(rows):
            cols = st.columns([v.width for v in row] + ([12 - sum(v.width for v in row)] if sum(v.width for v in row) < 12 else []))
            for col, v in zip(cols, row):
                with col:
                    try:
                        show(renderer.render(v), f"{prefix}-{si}-{ri}-{v.id}")
                    except Exception as exc:  # one broken visual must not break the page
                        st.error(f"{v.id}: {exc}")


def export_bar(board: Dashboard, prefix: str) -> None:
    c = st.columns([2, 1, 1, 1, 5])
    c[0].caption("Export page")
    for col, (fmt_, fn, mime) in zip(
        c[1:4],
        [("xlsx", export.dashboard_xlsx, None), ("html", export.dashboard_html, "text/html"), ("zip", export.dashboard_bundle, "application/zip")],
    ):
        k = f"{prefix}-export-{fmt_}"
        if col.button(fmt_.upper(), key=k):
            st.session_state[k + "-data"] = fn(renderer, board)
        if k + "-data" in st.session_state:
            col.download_button("Download", st.session_state[k + "-data"], f"{board.id}.{fmt_}", mime=mime, key=k + "-dl")


tabs = st.tabs([b.title for b in boards] + ["Spec editor", "Agent view"])
for tab, board in zip(tabs, boards):
    with tab:
        export_bar(board, board.id)
        show_dashboard(board, board.id)

designer = ViewDesigner(catalog, workbench.dashboard_dirs(), workbench.custom_dashboard_dir())

with tabs[-2]:
    st.markdown("Edit a dashboard spec, validate it against this data, preview and save it as a custom dashboard.")
    pick = st.selectbox("Start from", [b.id for b in boards])
    template = next(b for b in boards if b.id == pick)
    text = st.text_area("YAML", template.to_yaml(), height=420, key=f"yaml-{pick}")
    c1, c2 = st.columns(2)
    try:
        spec = yaml.safe_load(text)
        result = designer.validate_dashboard(spec)
    except yaml.YAMLError as exc:
        spec, result = None, {"ok": False, "errors": [str(exc)], "warnings": []}
    for e in result["errors"]:
        st.error(e)
    for w in result["warnings"]:
        st.warning(w)
    if result["ok"]:
        st.success("Valid")
        if c1.button("Save as custom dashboard"):
            saved = designer.save_dashboard(spec, overwrite=True)
            (st.success if saved["ok"] else st.error)(json.dumps(saved))
        with st.expander("Preview", expanded=True):
            show_dashboard(Dashboard.model_validate(spec), "preview")

with tabs[-1]:
    st.markdown(
        "This is **everything the agent can see**: names, units and structure. No values, case names, "
        "entity names or dates. The agent designs dashboards from this and never reads results."
    )
    if st.button("Write catalog.json for the MCP server"):
        out = workbench.root() / "catalog.json"
        out.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        st.success(str(out))
    st.json(catalog, expanded=False)
