"""Loads R reference fixtures and compares Curvis results with them.

A fixture (written by validation/r/R/fixture.R) holds an analysis' inputs, the outputs
R computed, the tolerance kind of each output, and the R and package versions used.
JSON has no NaN or infinity, so those are the strings "NaN", "Inf" and "-Inf".

Comparison rules, per value:

- NaN must be NaN, and an infinity the same infinity.
- Kind "exact" must be equal.
- Otherwise ``|actual - expected| <= tolerance * |expected|`` (purely relative, so an
  expected 0 needs an actual 0).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from tests.validation.tolerances import TOLERANCES, tolerance_for

SCHEMA_VERSION = 1
FIXTURE_DIR = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parents[2]

SPECIAL = {"NaN": math.nan, "Inf": math.inf, "-Inf": -math.inf}

# At most this many differing values are listed in a report.
MAX_REPORTED = 25


@dataclass(frozen=True)
class Fixture:
    path: Path
    analysis: str
    case: str
    description: str
    generated_by: dict[str, Any]
    inputs: dict[str, Any]
    expected: dict[str, npt.NDArray[np.float64]]
    kinds: dict[str, str]

    @property
    def id(self) -> str:
        return f"{self.analysis}/{self.case}"


@dataclass(frozen=True)
class Mismatch:
    output: str
    index: tuple[int, ...] | None  # None: the whole output (missing, extra, shape)
    expected: float | None = None
    actual: float | None = None
    tolerance: float | None = None
    source: str = ""  # the tolerance kind, or the exception that set it
    reason: str = ""

    @property
    def rel_error(self) -> float | None:
        if self.expected is None or self.actual is None:
            return None
        if not (math.isfinite(self.expected) and math.isfinite(self.actual)):
            return None
        if self.expected == 0:
            return math.inf if self.actual != 0 else 0.0
        return abs(self.actual - self.expected) / abs(self.expected)


def decode(value: Any) -> Any:
    """Turns fixture JSON into Python values: numeric lists become float64 arrays."""
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        if all(_is_number(item) for item in value):
            return np.array([_number(item) for item in value], dtype=np.float64)
        return [decode(item) for item in value]
    if isinstance(value, str) and value in SPECIAL:
        return SPECIAL[value]
    return value


def _is_number(value: Any) -> bool:
    if isinstance(value, str):
        return value in SPECIAL
    return isinstance(value, int | float) and not isinstance(value, bool)


def _number(value: Any) -> float:
    return SPECIAL[value] if isinstance(value, str) else float(value)


def load_fixture(path: Path) -> Fixture:
    """Reads and checks one fixture file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version {raw.get('schema_version')!r} is not supported")
    fixture = Fixture(
        path=path,
        analysis=raw["analysis"],
        case=raw["case"],
        description=raw["description"],
        generated_by=raw["generated_by"],
        inputs=decode(raw["inputs"]),
        expected={
            key: np.asarray(decode(v), dtype=np.float64) for key, v in raw["expected"].items()
        },
        kinds=raw["kinds"],
    )
    if (path.parent.name, path.stem) != (fixture.analysis, fixture.case):
        raise ValueError(f"{path}: file name does not match {fixture.id}")
    if set(fixture.kinds) != set(fixture.expected):
        raise ValueError(f"{path}: kinds and expected name different outputs")
    unknown = set(fixture.kinds.values()) - set(TOLERANCES)
    if unknown:
        raise ValueError(f"{path}: unknown tolerance kinds {sorted(unknown)}")
    return fixture


def fixture_paths(directory: Path = FIXTURE_DIR) -> list[Path]:
    return sorted(directory.glob("*/*.json"))


def compare(fixture: Fixture, actual: Mapping[str, npt.ArrayLike]) -> list[Mismatch]:
    """Every value where ``actual`` differs from the fixture beyond its tolerance."""
    mismatches: list[Mismatch] = []
    for output in sorted(set(actual) - set(fixture.expected)):
        mismatches.append(Mismatch(output, None, reason="not in the fixture"))
    for output, expected in fixture.expected.items():
        if output not in actual:
            mismatches.append(Mismatch(output, None, reason="missing from the Curvis result"))
            continue
        got = np.asarray(actual[output], dtype=np.float64)
        if got.shape != expected.shape:
            mismatches.append(
                Mismatch(output, None, reason=f"shape {got.shape}, expected {expected.shape}")
            )
            continue
        kind = fixture.kinds[output]
        tolerance, source = tolerance_for(fixture.id, output, kind)
        for index in np.ndindex(expected.shape):
            e, a = float(expected[index]), float(got[index])
            if not _within(e, a, tolerance):
                mismatches.append(Mismatch(output, index, e, a, tolerance, source))
    return mismatches


def _within(expected: float, actual: float, tolerance: float) -> bool:
    if math.isnan(expected) or math.isnan(actual):
        return math.isnan(expected) and math.isnan(actual)
    if math.isinf(expected) or math.isinf(actual):
        return expected == actual
    return abs(actual - expected) <= tolerance * abs(expected)


def _shown(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)


def format_report(fixture: Fixture, mismatches: list[Mismatch]) -> str:
    """A readable table of the differences between Curvis and R."""
    total = sum(v.size for v in fixture.expected.values())
    values = [m for m in mismatches if m.index is not None]
    whole = [m for m in mismatches if m.index is None]
    made = fixture.generated_by
    lines = [
        f"{fixture.id}: Curvis differs from R ({len(values)} of {total} values"
        + (f", {len(whole)} outputs" if whole else "")
        + ")",
        f"  {fixture.description}",
        f"  fixture: {_shown(fixture.path)}",
        f"  made by: {made.get('script')} with {made.get('r_version')}",
    ]
    for m in whole:
        lines.append(f"  {m.output}: {m.reason}")
    if values:
        rows = [("output", "index", "expected", "actual", "rel. error", "tolerance")]
        for m in values[:MAX_REPORTED]:
            assert m.index is not None
            rel = m.rel_error
            rows.append(
                (
                    m.output,
                    ",".join(str(i) for i in m.index),
                    repr(m.expected),
                    repr(m.actual),
                    "-" if rel is None else f"{rel:.2e}",
                    f"{m.tolerance:g} ({m.source})",
                )
            )
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for row in rows:
            lines.append(
                "  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True))
            )
        if len(values) > MAX_REPORTED:
            lines.append(f"  ... and {len(values) - MAX_REPORTED} more")
    return "\n".join(line.rstrip() for line in lines)
