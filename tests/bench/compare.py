"""Compare a benchmark run with the stored baseline; fail on a slowdown over 15%.

Usage (CI runs the benchmarks three times)::

    uv run pytest tests/bench --benchmark-only --benchmark-json=bench1.json   # and 2, 3
    uv run python -m tests.bench.compare bench*.json            # check
    uv run python -m tests.bench.compare bench*.json --update   # store as new baseline

Each benchmark is compared on its **minimum** time over all rounds of all given runs,
the statistic least affected by other load on a shared machine. Timings are first
divided by the calibration benchmark (tests/bench/test_calibration.py). That corrects
modest speed drift between CI runners, not the gap between unlike machines: one CPU
workload cannot stand in for I/O, process start-up and thread wake-up costs. So the
stored baseline must come from the CI runner (the ``benchmarks`` artifact); locally,
compare against a baseline of your own with ``--baseline``. A benchmark that is in the
baseline but missing from the run fails the check; a new benchmark is only reported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASELINE = Path(__file__).with_name("baseline.json")
CALIBRATION = "tests/bench/test_calibration.py::test_calibration"
THRESHOLD = 0.15
STAT = "min"


@dataclass(frozen=True)
class Row:
    name: str
    baseline: float | None  # seconds, as recorded
    current: float | None  # seconds, as measured
    ratio: float | None  # current / scaled baseline; > 1 is slower
    status: str  # "ok", "slower", "missing", "new"


def load(*paths: Path) -> dict[str, float]:
    """``{fullname: min seconds}`` over pytest-benchmark JSON files (or a baseline)."""
    result: dict[str, float] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for b in data["benchmarks"]:
            value = float(b["stats"][STAT])
            result[b["fullname"]] = min(value, result.get(b["fullname"], value))
    return result


def compare(
    baseline: dict[str, float],
    current: dict[str, float],
    threshold: float = THRESHOLD,
    normalize: bool = True,
) -> tuple[float, list[Row]]:
    """The machine-speed scale used and one row per benchmark."""
    scale = 1.0
    if normalize and CALIBRATION in baseline and CALIBRATION in current:
        scale = current[CALIBRATION] / baseline[CALIBRATION]
    rows = []
    for name in sorted(baseline.keys() | current.keys()):
        if name == CALIBRATION:
            continue
        base, now = baseline.get(name), current.get(name)
        if base is None:
            rows.append(Row(name, None, now, None, "new"))
        elif now is None:
            rows.append(Row(name, base, None, None, "missing"))
        else:
            ratio = now / (base * scale)
            rows.append(Row(name, base, now, ratio, "slower" if ratio > 1 + threshold else "ok"))
    return scale, rows


def failed(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.status in ("slower", "missing")]


def _ms(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds * 1000:.3f}"


def report(scale: float, rows: list[Row], threshold: float) -> str:
    lines = [
        f"Machine speed vs baseline (calibration): {scale:.2f}x the time",
        f"Comparing {STAT} times; fail above +{threshold:.0%}.",
        "",
        f"{'benchmark':<60} {'base ms':>10} {'now ms':>10} {'change':>8}  status",
    ]
    for r in rows:
        change = "-" if r.ratio is None else f"{r.ratio - 1:+.1%}"
        short = r.name.removeprefix("tests/bench/")
        lines.append(
            f"{short:<60} {_ms(r.baseline):>10} {_ms(r.current):>10} {change:>8}  {r.status}"
        )
    if not 0.75 <= scale <= 1.33:
        warning = (
            f"This machine runs the calibration {scale:.2f}x as long as the baseline "
            "machine; the comparison is unreliable. Record the baseline on this kind of machine."
        )
        prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "Warning: "
        lines += ["", prefix + warning]
    return "\n".join(lines)


def compact(paths: list[Path]) -> dict[str, Any]:
    """What is worth committing as a baseline from pytest-benchmark JSON files.

    Per benchmark: the min over all runs (what is compared) and each run's min and
    median (to see how noisy the machine was).
    """
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    data = runs[0]
    info = data.get("machine_info", {})
    best = load(*paths)
    per_run: dict[str, list[dict[str, float]]] = {}
    for run in runs:
        for b in run["benchmarks"]:
            stats = {k: b["stats"][k] for k in ("min", "median")}
            per_run.setdefault(b["fullname"], []).append(stats)
    names = {b["fullname"]: b["name"] for run in runs for b in run["benchmarks"]}
    return {
        "machine_info": {
            "cpu": info.get("cpu", {}).get("brand_raw"),
            "system": info.get("system"),
            "python": info.get("python_version"),
        },
        "commit": data.get("commit_info", {}).get("id"),
        "datetime": data.get("datetime"),
        "runs": len(runs),
        "benchmarks": [
            {
                "name": names[full],
                "fullname": full,
                "stats": {STAT: best[full]},
                "runs": per_run[full],
            }
            for full in sorted(best)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "current", type=Path, nargs="+", help="JSON file(s) from pytest --benchmark-json"
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--no-normalize", action="store_true", help="skip calibration")
    parser.add_argument("--update", action="store_true", help="store current as baseline")
    args = parser.parse_args(argv)

    if args.update:
        text = json.dumps(compact(args.current), indent=2) + "\n"
        args.baseline.write_text(text, encoding="utf-8")
        print(f"Stored {len(args.current)} run(s) as the baseline in {args.baseline}.")
        return 0

    scale, rows = compare(
        load(args.baseline), load(*args.current), args.threshold, not args.no_normalize
    )
    print(report(scale, rows, args.threshold))
    bad = failed(rows)
    if bad:
        print(
            f"\n{len(bad)} benchmark(s) slower than the baseline by more than "
            f"{args.threshold:.0%}, or missing."
        )
        return 1
    print("\nNo benchmark is slower than the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
