"""Tests of the validation harness itself: loading, comparing and reporting."""

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.validation import tolerances
from tests.validation.harness import compare, decode, format_report, load_fixture


def write(tmp_path: Path, **changes: Any) -> Path:
    fixture: dict[str, Any] = {
        "schema_version": 1,
        "analysis": "demo",
        "case": "one",
        "description": "A hand-made fixture.",
        "generated_by": {
            "script": "validation/r/scripts/demo.R",
            "r_version": "R version 4.3.3 (2024-02-29)",
            "platform": "x86_64-pc-linux-gnu",
            "packages": {"stats": "4.3.3"},
        },
        "inputs": {"x": [[1, "NaN"], [2.5, "-Inf"]]},
        "expected": {"n": [2, 1], "mean": [1.75, "Inf"], "p": [0.5, "NaN"]},
        "kinds": {"n": "exact", "mean": "statistic", "p": "p_value"},
    }
    fixture.update(changes)
    path = tmp_path / fixture["analysis"] / f"{fixture['case']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    return path


GOOD = {"n": [2, 1], "mean": [1.75, math.inf], "p": [0.5, math.nan]}


def test_decode_special_values() -> None:
    columns = decode([[1, "NaN"], ["Inf", "-Inf"]])
    assert columns[0][0] == 1.0 and math.isnan(columns[0][1])
    assert columns[1].tolist() == [math.inf, -math.inf]
    assert decode({"name": "NaN rows", "flag": True}) == {"name": "NaN rows", "flag": True}


def test_load_fixture(tmp_path: Path) -> None:
    fixture = load_fixture(write(tmp_path))
    assert fixture.id == "demo/one"
    assert fixture.expected["n"].dtype == np.float64
    assert len(fixture.inputs["x"]) == 2


def test_matching_result_passes(tmp_path: Path) -> None:
    assert compare(load_fixture(write(tmp_path)), GOOD) == []


def test_relative_tolerance_boundary(tmp_path: Path) -> None:
    fixture = load_fixture(write(tmp_path))
    inside = dict(GOOD, mean=[1.75 * (1 + 0.9e-8), math.inf])
    outside = dict(GOOD, mean=[1.75 * (1 + 1.1e-8), math.inf])
    assert compare(fixture, inside) == []
    [mismatch] = compare(fixture, outside)
    assert (mismatch.output, mismatch.index, mismatch.source) == ("mean", (0,), "statistic")


def test_exact_kind_needs_equality(tmp_path: Path) -> None:
    [mismatch] = compare(load_fixture(write(tmp_path)), dict(GOOD, n=[2, 1 + 1e-15]))
    assert mismatch.output == "n"


@pytest.mark.parametrize(
    "mean, p",
    [
        ([1.75, 1e308], [0.5, math.nan]),  # finite where R has Inf
        ([1.75, -math.inf], [0.5, math.nan]),  # the other infinity
        ([1.75, math.inf], [0.5, 0.5]),  # a number where R has NaN
        ([math.nan, math.inf], [0.5, math.nan]),  # NaN where R has a number
    ],
)
def test_special_values_must_match(tmp_path: Path, mean: list[float], p: list[float]) -> None:
    assert len(compare(load_fixture(write(tmp_path)), dict(GOOD, mean=mean, p=p))) == 1


def test_expected_zero_needs_zero(tmp_path: Path) -> None:
    fixture = load_fixture(write(tmp_path, expected={"n": [2, 1], "mean": [0, 0], "p": [0, 1]}))
    assert len(compare(fixture, {"n": [2, 1], "mean": [0.0, 1e-300], "p": [0, 1]})) == 1


def test_missing_extra_and_shape(tmp_path: Path) -> None:
    fixture = load_fixture(write(tmp_path))
    result = {"n": [2, 1, 0], "p": GOOD["p"], "extra": [1.0]}
    reasons = {m.output: m.reason for m in compare(fixture, result)}
    assert reasons == {
        "n": "shape (3,), expected (2,)",
        "mean": "missing from the Curvis result",
        "extra": "not in the fixture",
    }


def test_exception_overrides_the_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tolerances.EXCEPTIONS, "demo/*:mean", 1e-3)
    fixture = load_fixture(write(tmp_path))
    assert compare(fixture, dict(GOOD, mean=[1.7505, math.inf])) == []
    [mismatch] = compare(fixture, dict(GOOD, mean=[1.76, math.inf]))
    assert mismatch.source == "exception demo/*:mean"


def test_report_is_readable(tmp_path: Path) -> None:
    fixture = load_fixture(write(tmp_path))
    result = {"n": [2, 1], "mean": [1.7500001, math.inf], "p": [0.5, 0.25]}
    report = format_report(fixture, compare(fixture, result))
    lines = report.splitlines()
    assert lines[0] == "demo/one: Curvis differs from R (2 of 6 values)"
    assert "made by: validation/r/scripts/demo.R with R version 4.3.3" in report
    header, first, second = lines[-3:]
    assert header.split() == ["output", "index", "expected", "actual", "rel.", "error", "tolerance"]
    assert first.split() == ["mean", "0", "1.75", "1.7500001", "5.71e-08", "1e-08", "(statistic)"]
    assert second.split() == ["p", "1", "nan", "0.25", "-", "1e-08", "(p_value)"]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"schema_version": 2}, "schema_version"),
        ({"kinds": {"n": "exact", "mean": "statistic"}}, "different outputs"),
        ({"kinds": {"n": "exact", "mean": "close", "p": "p_value"}}, "unknown tolerance"),
    ],
)
def test_bad_fixtures_are_rejected(tmp_path: Path, changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_fixture(write(tmp_path, **changes))


def test_file_name_must_match_the_case(tmp_path: Path) -> None:
    path = write(tmp_path)
    renamed = path.rename(path.with_name("other.json"))
    with pytest.raises(ValueError, match="file name"):
        load_fixture(renamed)
