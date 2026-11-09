from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AUTOGRATION_RESULTS_HOME", str(ROOT))


@pytest.fixture(scope="session")
def example_dir(tmp_path_factory) -> Path:
    from autogration_results.examples import write_example

    return write_example(tmp_path_factory.mktemp("example"))


@pytest.fixture(scope="session")
def store(example_dir):
    from autogration_results import workbench
    from autogration_results.engine import ResultStore

    return ResultStore.from_folder(example_dir, workbench.registry())


@pytest.fixture(scope="session")
def boards():
    from autogration_results import workbench
    from autogration_results.spec import load_dashboards

    return load_dashboards(*workbench.dashboard_dirs())
