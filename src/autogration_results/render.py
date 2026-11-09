"""Turn a visual spec + filter context into a Plotly figure, KPI cards or a table.

Rendering is pure Python so the same output (``figure.to_json()``) can feed the Streamlit
workbench, the Autogration React app and exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from autogration_results.engine import ResultStore, Unavailable
from autogration_results.health import HealthConfig, run_checks
from autogration_results.spec import Dashboard, FilterContext, Visual, first_available

ADDITIVE = {"rate", "volume", "cumulative", "count"}
BASELINE_COLOR = "#3b3b3b"
TEMPLATE = "plotly_white"


@dataclass
class Rendered:
    visual: Visual
    kind: Literal["figure", "kpis", "table", "markdown", "unavailable"]
    title: str
    figure: go.Figure | None = None
    table: pd.DataFrame | None = None
    kpis: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    data: pd.DataFrame | None = None


def resolve_context(store: ResultStore, ctx: FilterContext | None) -> FilterContext:
    ctx = (ctx or FilterContext()).model_copy(deep=True)
    completed = store.case_names()
    ctx.cases = [c for c in (ctx.cases or completed) if c in completed]
    ctx.baseline = ctx.baseline if ctx.baseline in completed else store.baseline()
    if ctx.focus not in completed:
        others = [c for c in ctx.cases if c != ctx.baseline]
        ctx.focus = others[0] if others else ctx.baseline
    return ctx


def case_colors(store: ResultStore, baseline: str | None) -> dict[str, str]:
    palette = px.colors.qualitative.Plotly + px.colors.qualitative.D3
    names = [c for c in store.case_names(completed_only=False) if c != baseline]
    colors = {c: palette[i % len(palette)] for i, c in enumerate(names)}
    if baseline:
        colors[baseline] = BASELINE_COLOR
    return colors


class Renderer:
    def __init__(self, store: ResultStore, ctx: FilterContext | None = None, health_config: HealthConfig | None = None):
        self.store = store
        self.ctx = resolve_context(store, ctx)
        self.colors = case_colors(store, self.ctx.baseline)
        self.health_config = health_config
        self._health: pd.DataFrame | None = None

    # ----------------------------------------------------------------- helpers
    @property
    def health(self) -> pd.DataFrame:
        if self._health is None:
            self._health = run_checks(self.store, self.health_config)
        return self._health

    def scope(self, v: Visual) -> list[str]:
        ctx = self.ctx
        if v.cases == "focus":
            return [ctx.focus] if ctx.focus else []
        if v.cases == "pair" or v.compare == "delta":
            return list(dict.fromkeys(c for c in (ctx.focus, ctx.baseline) if c))
        if v.cases == "all":
            return self.store.case_names()
        return list(ctx.cases or [])

    def query(self, v: Visual, measure: str, dims: list[str], cases: list[str] | None = None, grain: str | None = None) -> pd.DataFrame:
        filters = {**self.ctx.entity, **v.filters}
        return self.store.query(
            measure,
            dims,
            grain=grain or v.grain,
            cases=self.scope(v) if cases is None else cases,
            filters=filters,
            date_from=self.ctx.date_from,
            date_to=self.ctx.date_to,
        )

    def label(self, measure: str) -> str:
        m = self.store.registry[measure]
        return f"{m.label} [{m.unit}]"

    def title(self, v: Visual) -> str:
        if v.title:
            return v.title
        if v.measure:
            return self.label(v.measure) + (" - change vs baseline" if v.compare == "delta" else "")
        return v.id

    def _delta(self, df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        focus, base = self.ctx.focus, self.ctx.baseline
        wide = df.pivot_table(index=keys or None, columns="case", values="value", aggfunc="sum") if keys else df.set_index("case")[["value"]].T
        if focus not in wide or base not in wide:
            return pd.DataFrame(columns=[*keys, "value"])
        out = (wide[focus].fillna(0) - wide[base].fillna(0)).rename("value")
        return out.reset_index() if keys else pd.DataFrame({"value": [out.iloc[0]]})

    def _top_n(self, df: pd.DataFrame, v: Visual, kind: str) -> pd.DataFrame:
        if not (v.top_n and v.color and v.color in df):
            return df
        rank = df.groupby(v.color)["value"].apply(lambda s: s.abs().sum()).sort_values(ascending=False)
        keep = set(rank.index[: v.top_n])
        if len(rank) <= v.top_n:
            return df
        rest = df[~df[v.color].isin(keep)]
        df = df[df[v.color].isin(keep)]
        if kind in ADDITIVE:
            other = rest.groupby([c for c in df.columns if c not in (v.color, "value")], dropna=False)["value"].sum().reset_index()
            other[v.color] = f"Other ({len(rank) - v.top_n})"
            df = pd.concat([df, other], ignore_index=True)
        return df

    def _fig(self, fig: go.Figure, v: Visual, ytitle: str) -> go.Figure:
        fig.update_layout(template=TEMPLATE, title=None, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="", height=360)
        if v.type != "bar":
            fig.update_yaxes(title=ytitle)
            fig.update_xaxes(title=None if v.x == "period" else v.x)
        return fig

    # ----------------------------------------------------------------- render
    def render(self, v: Visual) -> Rendered:
        title = self.title(v)
        try:
            v = self._resolve_dims(v)
            handler = getattr(self, f"_render_{v.type}", None) or self._render_xy
            return handler(v, title)
        except Unavailable as exc:
            return Rendered(v, "unavailable", title, text=str(exc))

    def _resolve_dims(self, v: Visual) -> Visual:
        available = set(self.store.dimensions())
        x = first_available(v.x, available)
        color = first_available(v.color, available)
        for name, spec, hit in (("x", v.x, x), ("color", v.color, color)):
            if spec is not None and hit is None:
                raise Unavailable(f"{name} needs {' or '.join(spec)} in the reference data")
        return v.model_copy(update={"x": x, "color": color})

    def render_dashboard(self, board: Dashboard) -> list[tuple[str, list[Rendered]]]:
        return [(s.title, [self.render(v) for v in s.visuals]) for s in board.sections]

    def _render_note(self, v: Visual, title: str) -> Rendered:
        return Rendered(v, "markdown", v.title, text=v.note or "")

    def _render_xy(self, v: Visual, title: str) -> Rendered:
        m = self.store.registry[v.measure]
        dims = [v.x] + ([v.color] if v.color and v.color != v.x else [])
        if v.compare == "delta":
            df = self._delta(self.query(v, v.measure, list(dict.fromkeys([*dims, "case"]))), [d for d in dims if d != "case"])
        else:
            df = self.query(v, v.measure, dims)
        df = self._top_n(df, v, m.kind)
        if v.sort != "none":
            df = df.sort_values("value", ascending=v.sort == "asc")
        color = v.color if v.color in df else None
        cmap = self.colors if color == "case" else None
        common = dict(x=v.x, y="value", color=color, color_discrete_map=cmap)
        if v.type == "line":
            fig = px.line(df, **common, markers=v.grain == "year")
        elif v.type == "area":
            fig = px.area(df, **common)
        elif v.type == "column":
            fig = px.bar(df, **common)
        elif v.type == "bar":
            fig = px.bar(df, x="value", y=v.x, color=color, color_discrete_map=cmap, orientation="h")
            fig.update_yaxes(categoryorder="total ascending", title=None)
            fig.update_xaxes(title=self.label(v.measure))
        elif v.type == "heatmap":
            wide = df.pivot_table(index=v.color, columns=v.x, values="value", aggfunc="sum")
            fig = go.Figure(go.Heatmap(z=wide.values, x=wide.columns, y=wide.index.astype(str), colorscale="Viridis", colorbar=dict(title=m.unit)))
        else:
            raise ValueError(f"no renderer for {v.type}")
        if v.compare == "delta":
            fig.add_hline(y=0, line_color="#999", line_width=1)
        return Rendered(v, "figure", title, figure=self._fig(fig, v, self.label(v.measure)), data=df)

    _render_line = _render_area = _render_column = _render_bar = _render_heatmap = _render_xy

    def _stat(self, v: Visual, measure: str, case: str) -> float | None:
        if v.stat == "total":
            values = self.query(v, measure, [], cases=[case])["value"].dropna()
            return float(values.iloc[0]) if len(values) else None
        series = self.query(v, measure, ["period"], cases=[case])["value"].dropna()
        if series.empty:
            return None
        return float(series.iloc[-1] if v.stat == "last" else getattr(series, v.stat)())

    def _render_kpi(self, v: Visual, title: str) -> Rendered:
        ctx, cards = self.ctx, []
        for mid in v.measures:
            m = self.store.registry[mid]
            ok, reason = self.store.availability(m)
            card: dict[str, Any] = {"measure": mid, "label": m.label, "unit": m.unit, "stat": v.stat, "case": ctx.focus, "placeholder": m.placeholder}
            if not ok:
                cards.append({**card, "value": None, "note": reason})
                continue
            card["value"] = self._stat(v, mid, ctx.focus) if ctx.focus else None
            if v.compare == "delta" and ctx.baseline and ctx.baseline != ctx.focus:
                base = self._stat(v, mid, ctx.baseline)
                card["baseline"] = base
                if card["value"] is not None and base is not None:
                    card["delta"] = card["value"] - base
                    card["delta_pct"] = card["delta"] / base * 100 if base else None
                    if m.higher_is_better is not None and card["delta"]:
                        card["better"] = (card["delta"] > 0) == m.higher_is_better
            cards.append(card)
        return Rendered(v, "kpis", v.title or "Key figures", kpis=cards, data=pd.DataFrame(cards))

    def _render_table(self, v: Visual, title: str) -> Rendered:
        out: pd.DataFrame | None = None
        missing = []
        for mid in v.measures:
            try:
                df = self.query(v, mid, v.rows).rename(columns={"value": self.label(mid)})
            except Unavailable as exc:
                missing.append(str(exc))
                continue
            out = df if out is None else out.merge(df, on=v.rows, how="outer")
        if out is None:
            return Rendered(v, "unavailable", title, text="; ".join(missing))
        if "period" in out:
            out["period"] = pd.to_datetime(out["period"]).dt.strftime({"year": "%Y", "quarter": "%Y-%m", "month": "%Y-%m"}.get(v.grain, "%Y-%m-%d"))
        if v.top_n:
            out = out.head(v.top_n)
        return Rendered(v, "table", title, table=out, data=out, text="; ".join(missing))

    def _render_waterfall(self, v: Visual, title: str) -> Rendered:
        m = self.store.registry[v.measure]
        ctx = self.ctx
        steps = self._delta(self.query(v, v.measure, [v.x, "case"]), [v.x]).sort_values("value", key=abs, ascending=False)
        base = self.query(v, v.measure, [], cases=[ctx.baseline])["value"].sum()
        focus = self.query(v, v.measure, [], cases=[ctx.focus])["value"].sum()
        fig = go.Figure(
            go.Waterfall(
                x=[f"{ctx.baseline}", *steps[v.x].astype(str), f"{ctx.focus}"],
                measure=["absolute", *["relative"] * len(steps), "total"],
                y=[base, *steps["value"], focus],
                increasing=dict(marker_color="#2ca02c"),
                decreasing=dict(marker_color="#d62728"),
                totals=dict(marker_color="#1f77b4"),
            )
        )
        r = Rendered(v, "figure", title or f"{m.label}: {ctx.baseline} -> {ctx.focus}", figure=self._fig(fig, v, self.label(v.measure)), data=steps)
        if m.kind not in ADDITIVE:
            r.text = f"{m.kind} measures are not additive; steps do not sum to the total."
        return r

    def _render_fan(self, v: Visual, title: str) -> Rendered:
        df = self.query(v, v.measure, ["period", "case"])
        q = sorted(v.quantiles)
        band = df.groupby("period")["value"].quantile(q).unstack()
        band.columns = [f"P{round(100 - x * 100)}" for x in q]  # exceedance convention: low = P90
        fig = go.Figure()
        lo, hi = band.columns[0], band.columns[-1]
        fig.add_trace(go.Scatter(x=band.index, y=band[hi], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=band.index, y=band[lo], fill="tonexty", fillcolor="rgba(31,119,180,0.2)", line=dict(width=0), name=f"{lo}-{hi}"))
        for col in band.columns[1:-1]:
            fig.add_trace(go.Scatter(x=band.index, y=band[col], name=col, line=dict(color="#1f77b4")))
        if self.ctx.baseline:
            b = df[df.case == self.ctx.baseline]
            fig.add_trace(go.Scatter(x=b.period, y=b.value, name=f"Baseline ({self.ctx.baseline})", line=dict(color=BASELINE_COLOR, dash="dash")))
        r = Rendered(v, "figure", title, figure=self._fig(fig, v, self.label(v.measure)), data=band.reset_index())
        r.text = f"Spread across {df.case.nunique()} cases (P90 = low, P10 = high)."
        return r

    def _render_tornado(self, v: Visual, title: str) -> Rendered:
        axes = self.store.case_axes
        if not axes:
            return Rendered(v, "unavailable", title, text="cases.csv has no assumption axis columns")
        totals = self.query(v, v.measure, ["case"])
        cases = self.store.cases().set_index("case_name")
        totals = totals.join(cases[axes], on="case")
        ref = totals.loc[totals.case == self.ctx.baseline, "value"]
        ref = float(ref.iloc[0]) if len(ref) else float(totals.value.median())
        rows = []
        for axis in axes:
            lv = totals.groupby(axis)["value"].mean()
            if len(lv) > 1:
                rows.append({"axis": axis, "low": lv.min(), "high": lv.max(), "low_level": lv.idxmin(), "high_level": lv.idxmax()})
        t = pd.DataFrame(rows).assign(range=lambda d: d.high - d.low).sort_values("range")
        fig = go.Figure(
            go.Bar(
                y=t.axis,
                x=t.high - t.low,
                base=t.low,
                orientation="h",
                text=[f"{a} .. {b}" for a, b in zip(t.low_level, t.high_level)],
                marker_color="#1f77b4",
            )
        )
        fig.add_vline(x=ref, line_dash="dash", line_color=BASELINE_COLOR, annotation_text="baseline")
        fig.update_layout(template=TEMPLATE, margin=dict(l=10, r=10, t=10, b=10), height=360, xaxis_title=self.label(v.measure))
        r = Rendered(v, "figure", title or f"Drivers of {self.label(v.measure)}", figure=fig, data=t)
        r.text = "Mean of the measure total per assumption level, across the selected cases."
        return r

    def _render_s_curve(self, v: Visual, title: str) -> Rendered:
        totals = self.query(v, v.measure, ["case"]).sort_values("value", ascending=False).reset_index(drop=True)
        n = len(totals)
        totals["exceedance_pct"] = (np.arange(1, n + 1) / n) * 100
        fig = go.Figure(go.Scatter(x=totals.value, y=totals.exceedance_pct, mode="lines+markers", line_shape="hv", text=totals.case, name="cases"))
        for p, q in (("P90", 0.1), ("P50", 0.5), ("P10", 0.9)):
            if n:
                fig.add_vline(x=float(np.quantile(totals.value, q)), line_dash="dot", annotation_text=p)
        fig.update_layout(template=TEMPLATE, margin=dict(l=10, r=10, t=10, b=10), height=360, xaxis_title=self.label(v.measure), yaxis_title="Probability of exceeding [%]")
        return Rendered(v, "figure", title, figure=fig, data=totals)

    def _render_health_summary(self, v: Visual, title: str) -> Rendered:
        h = self.health
        cards = [
            {"label": "Critical findings", "value": int((h.severity == "critical").sum()), "unit": "", "severity": "critical"},
            {"label": "Warnings", "value": int((h.severity == "warning").sum()), "unit": "", "severity": "warning"},
            {"label": "Failed cases", "value": int((h.check == "failed_case").sum()), "unit": "", "severity": "critical"},
            {"label": "Cases with critical findings", "value": int(h.loc[h.severity == "critical", "case"].nunique()), "unit": "", "severity": "critical"},
        ]
        return Rendered(v, "kpis", v.title or "Run health", kpis=cards, data=pd.DataFrame(cards))

    def _render_health_matrix(self, v: Visual, title: str) -> Rendered:
        h = self.health.dropna(subset=["case"])
        cases = self.store.case_names(completed_only=False)
        wide = h.pivot_table(index="case", columns="check", values="count", aggfunc="size", fill_value=0).reindex(cases, fill_value=0)
        if wide.empty:
            return Rendered(v, "markdown", title, text="No findings.")
        fig = go.Figure(go.Heatmap(z=wide.values, x=wide.columns, y=wide.index, colorscale="Reds", text=wide.values, texttemplate="%{text}", showscale=False))
        fig.update_layout(template=TEMPLATE, margin=dict(l=10, r=10, t=10, b=10), height=max(260, 28 * len(wide)))
        return Rendered(v, "figure", title or "Findings per case", figure=fig, data=wide.reset_index())

    def _render_health_table(self, v: Visual, title: str) -> Rendered:
        h = self.health.head(v.top_n or 200)
        return Rendered(v, "table", title or "Findings", table=h, data=h)
