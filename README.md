# autogration-results

Definitions of the **Results** pages in Autogration: which numbers to calculate (measures),
which pages and charts to show (dashboards) and when a run counts as suspicious (health
checks). Plus a local workbench to preview them on example or exported data.

You can change the results here without touching the Autogration code. The Autogration team
exports the definitions into the web app.

## Setup

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync --group dev --extra app
uv run --extra app streamlit run app/viewer.py      # preview at http://localhost:8501
uv run pytest -q                                    # checks
```

The viewer opens a synthetic example study. It can also read a GAP CSV folder (the Power BI
share layout) or results exported from Autogration.

## What to edit

| File | What it controls |
|---|---|
| `specs/measures/*.yaml` | Measures: which result property, unit scaling, how to sum over wells and time |
| `specs/dashboards/*.yaml` | The pages: sections, charts, tables, KPI cards and their order |
| `specs/health.yaml` | Thresholds of the Run health checks |

`specs/dashboards/custom/` holds drafts saved from the viewer's spec editor. Move a file up
into `specs/dashboards/` to publish it.

Measure `kind` decides how values combine, so ratios are never averaged:

| kind | across wells | across time |
|---|---|---|
| rate | sum | calendar-day weighted mean |
| volume | sum | rate x days |
| cumulative | sum | last value |
| ratio | sum(num) / sum(den) | sum(num x days) / sum(den x days) |
| state | mean | days-weighted mean |
| count | wells with value > 0 | max |

A dimension given as a list, e.g. `color: [entity.field, identifier]`, means "the first one the
data has". Field, group and license come from reference data, which Autogration does not export.

## Getting changes into Autogration

1. Work on a branch here and open a pull request; `uv run pytest -q` must pass.
2. After merge, an Autogration developer runs:
   ```sh
   uv run autogration-results export-specs --frontend <path to autogration_frontend>
   ```
   This checks that the web app can show everything and writes
   `frontend/src/pages/results/engine/specs.gen.ts` in the frontend repo.
3. They run the frontend tests and open a frontend pull request into `development`.

The web app supports a subset of the workbench. `export-specs` refuses specs that use `stat`
other than `total`/`max`, `grain: timestamp`, `sort`, `filters`, custom `quantiles`,
`case.status`, or measure `weight`.
