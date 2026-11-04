"""The NIST model equations in nist_models.py are copied correctly: at the certified
parameters, each gives the certified residual sum of squares."""

from pathlib import Path

import numpy as np
import pytest

from tests.validation.harness import fixture_paths, load_fixture
from tests.validation.nist_models import EQUATIONS, NistModel

NLS = [p for p in fixture_paths() if p.parent.name == "nist_nls" and p.stem.endswith("_start2")]


def test_every_dataset_has_an_equation() -> None:
    datasets = {load_fixture(p).inputs["dataset"] for p in NLS}
    assert datasets == set(EQUATIONS)


@pytest.mark.parametrize("path", NLS, ids=lambda p: p.stem.removesuffix("_start2"))
def test_certified_parameters_give_the_certified_rss(path: Path) -> None:
    fixture = load_fixture(path)
    x, y = fixture.inputs["x"], fixture.inputs["y"]
    certified = fixture.expected["parameters"]
    model = NistModel(fixture.inputs["dataset"], len(certified))
    residuals = y - model.f(x, certified)
    if "rss" not in fixture.expected:  # residuals at rounding level (Lanczos1)
        assert np.max(np.abs(residuals)) < 1e-10 * np.max(np.abs(y))
        return
    rss = float(np.sum(residuals**2))
    # The certified parameters are rounded to 11 digits, so RSS can differ slightly.
    assert rss == pytest.approx(float(fixture.expected["rss"]), rel=1e-7)
