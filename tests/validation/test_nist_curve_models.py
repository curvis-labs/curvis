"""The step 3.3 kinetics models on the NIST StRD datasets they contain, against the
certified values (tests/validation/fixtures/nist_nls), at the standard tolerances.

- Misra1a and BoxBOD: y = b1 (1 - exp(-b2 x)) is the exponential association with
  Y0 fixed at 0, Plateau = b1, K = b2.
- Misra1d: y = b1 b2 x / (1 + b2 x) is Michaelis-Menten with Vmax = b1, Km = 1 / b2;
  SE(b2) = SE(Km) / Km² exactly (the asymptotic covariance transforms with the
  Jacobian of the reparametrisation).

Each is fitted from NIST's two starting values and from Curvis' automatic ones.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from curvis.analysis.fit.engine import Constraints, NonlinearFit, fit_model
from curvis.analysis.fit.kinetics import EXP_ASSOCIATION, MICHAELIS_MENTEN
from curvis.analysis.fit.nonlinear import fit_curve
from curvis.analysis.fit.points import Points
from tests.validation.harness import FIXTURE_DIR, compare, format_report, load_fixture

DATASETS = ("misra1a", "boxbod", "misra1d")
CASES = [(d, s) for d in DATASETS for s in ("start1", "start2", "auto")]


def nist_values(dataset: str, f: NonlinearFit) -> Mapping[str, Any]:
    if dataset == "misra1d":
        vmax, km = f.values
        se_vmax, se_km = f.se
        return {
            "parameters": [vmax, 1 / km],
            "se": [se_vmax, se_km / km**2],
            "rss": f.rss,
            "sy_x": f.sy_x,
            "df": f.df,
        }
    _y0, plateau, k = f.values
    return {"parameters": [plateau, k], "se": list(f.se[1:]), "rss": f.rss, "sy_x": f.sy_x,
            "df": f.df}  # fmt: skip


@pytest.mark.parametrize(("dataset", "start"), CASES, ids=[f"{d}-{s}" for d, s in CASES])
def test_kinetics_models_match_nist(dataset: str, start: str) -> None:
    number = "1" if start == "auto" else start[-1]
    path: Path = FIXTURE_DIR / "nist_nls" / f"{dataset}_start{number}.json"
    fixture = load_fixture(path)
    points = Points(np.asarray(fixture.inputs["x"]), np.asarray(fixture.inputs["y"]))
    b = np.asarray(fixture.inputs["start"], dtype=np.float64)
    if dataset == "misra1d":
        spec, fixed, p0 = MICHAELIS_MENTEN, {}, np.array([b[0], 1 / b[1]])
    else:
        spec, fixed, p0 = EXP_ASSOCIATION, {"y0": 0.0}, np.array([0.0, b[0], b[1]])
    if start == "auto":
        f = fit_curve(spec, points, fixed)
    else:
        f = fit_model(spec.model, points, Constraints(fixed), p0)
    assert f.ok, f.message
    mismatches = compare(fixture, nist_values(dataset, f))
    assert not mismatches, format_report(fixture, mismatches)
