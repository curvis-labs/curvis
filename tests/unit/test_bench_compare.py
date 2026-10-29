"""Tests for the benchmark regression gate (tests/bench/compare.py)."""

import json
from pathlib import Path

import pytest

from tests.bench import compare

CAL = compare.CALIBRATION


def write_run(path: Path, mins: dict[str, float]) -> Path:
    """A minimal pytest-benchmark JSON file."""
    benchmarks = [
        {"name": n.split("::")[-1], "fullname": n, "stats": {"min": v, "median": v * 1.1}}
        for n, v in mins.items()
    ]
    path.write_text(json.dumps({"machine_info": {}, "benchmarks": benchmarks}))
    return path


def statuses(rows: list[compare.Row]) -> dict[str, str]:
    return {r.name: r.status for r in rows}


def test_within_threshold_passes() -> None:
    scale, rows = compare.compare({CAL: 1.0, "a": 1.0}, {CAL: 1.0, "a": 1.14})
    assert scale == 1.0
    assert statuses(rows) == {"a": "ok"}
    assert compare.failed(rows) == []


def test_more_than_15_percent_slower_fails() -> None:
    _, rows = compare.compare({CAL: 1.0, "a": 1.0, "b": 1.0}, {CAL: 1.0, "a": 1.16, "b": 0.5})
    assert statuses(rows) == {"a": "slower", "b": "ok"}
    assert [r.name for r in compare.failed(rows)] == ["a"]


def test_timings_are_scaled_by_the_calibration() -> None:
    baseline = {CAL: 1.0, "a": 1.0}
    slow_machine = {CAL: 2.0, "a": 2.2}  # 10% slower once machine speed is taken out
    scale, rows = compare.compare(baseline, slow_machine)
    assert scale == 2.0
    assert rows[0].ratio == pytest.approx(1.1)
    assert compare.failed(rows) == []
    _, raw = compare.compare(baseline, slow_machine, normalize=False)
    assert statuses(raw) == {"a": "slower"}


def test_missing_benchmark_fails_and_new_one_is_reported() -> None:
    _, rows = compare.compare({CAL: 1.0, "gone": 1.0}, {CAL: 1.0, "new": 1.0})
    assert statuses(rows) == {"gone": "missing", "new": "new"}
    assert [r.name for r in compare.failed(rows)] == ["gone"]


def test_custom_threshold() -> None:
    _, rows = compare.compare({"a": 1.0}, {"a": 1.25}, threshold=0.3)
    assert compare.failed(rows) == []


def test_load_takes_the_min_over_runs(tmp_path: Path) -> None:
    one = write_run(tmp_path / "1.json", {"a": 3.0, "b": 1.0})
    two = write_run(tmp_path / "2.json", {"a": 2.0, "b": 4.0})
    assert compare.load(one, two) == {"a": 2.0, "b": 1.0}


def test_main_check_update_and_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.json"
    first = write_run(tmp_path / "1.json", {CAL: 0.01, "t::a": 0.002})
    second = write_run(tmp_path / "2.json", {CAL: 0.01, "t::a": 0.001})
    assert compare.main([str(first), str(second), "--update", "--baseline", str(baseline)]) == 0
    stored = json.loads(baseline.read_text())
    assert stored["runs"] == 2
    assert compare.load(baseline) == {CAL: 0.01, "t::a": 0.001}

    same = write_run(tmp_path / "3.json", {CAL: 0.01, "t::a": 0.00105})
    assert compare.main([str(same), "--baseline", str(baseline)]) == 0
    assert "No benchmark is slower" in capsys.readouterr().out

    slower = write_run(tmp_path / "4.json", {CAL: 0.01, "t::a": 0.0013})
    assert compare.main([str(slower), "--baseline", str(baseline)]) == 1
    out = capsys.readouterr().out
    assert "+30.0%" in out and "slower" in out


def test_stored_baseline_covers_every_benchmark() -> None:
    """The committed baseline must list each benchmark in tests/bench (else none is gated)."""
    names = set(compare.load(compare.BASELINE))
    bench_dir = Path(compare.__file__).parent
    for path in sorted(bench_dir.glob("test_*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("def test_"):
                test = line.removeprefix("def ").split("(")[0]
                assert f"tests/bench/{path.name}::{test}" in names, f"{test} not in baseline"
