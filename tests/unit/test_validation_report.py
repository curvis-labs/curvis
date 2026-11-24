"""The validation report script (scripts/validation_report.py): every analysis with
fixtures is described, and the Markdown and PDF are written."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.validation.harness import Difference, fixture_paths

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def report() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validation_report", ROOT / "scripts" / "validation_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validation_report"] = module
    spec.loader.exec_module(module)
    return module


def test_every_analysis_with_fixtures_has_a_reference(report: ModuleType) -> None:
    analyses = {path.parent.name for path in fixture_paths()}
    assert set(report.REFERENCES) == analyses
    assert {ref.group for ref in report.REFERENCES.values()} <= set(report.GROUPS)


def summary(report: ModuleType, ratio: float) -> ModuleType:
    a = report.AnalysisSummary("t_test", script="validation/r/scripts/t_test.R", fixtures=2)
    a.r_versions.add("4.6.1")
    a.packages["stats"].add("4.6.1")
    out = report.OutputSummary("p_value", 1e-8)
    out.add(
        "basic", Difference("p_value", "p_value", 3, ratio * 1e-8, 1e-8, "p_value", ratio, False)
    )
    a.outputs["p_value"] = out
    return a


def test_report_lists_the_analysis_and_its_agreement(report: ModuleType) -> None:
    run = report.PytestRun(10, 0, 0, 1, {"test_fixtures.py": (10, 1)})
    text = report.markdown({"t_test": summary(report, 0.25)}, run)
    assert "**Result: every analysis matches its reference.**" in text
    assert (
        "| Unpaired t test, Welch's t test, paired t test | `stats::t.test` | 2 | 3 | 25% | Pass |"
        in text
    )
    assert "| `p_value` | p_value | 3 | 2.5e-09 | 1e-08 | 25% | basic |" in text
    assert "## Approved rules and exceptions" in text
    assert "`biphasic/noisy_04:ec50_2`" in text  # copied from TOLERANCES.md


def test_report_shows_a_failure(report: ModuleType) -> None:
    text = report.markdown({"t_test": summary(report, 3.0)}, None)
    assert "FAILURES, see below" in text and "**FAIL**" in text
    assert "## Test run" not in text


def test_pdf_is_written(report: ModuleType, tmp_path: Path) -> None:
    text = report.markdown({"t_test": summary(report, 0.25)}, None)
    path = tmp_path / "report.pdf"
    report.write_pdf(text, path)
    assert path.read_bytes().startswith(b"%PDF")
