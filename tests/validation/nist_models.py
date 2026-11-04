"""The models of the NIST StRD nonlinear regression datasets, as Curvis FitModels.

Each equation is copied from the "Model:" section of ``validation/r/nist/<name>.dat``.
``test_nist_models.py`` checks every one: its residual sum of squares at the certified
parameters must equal the certified RSS. The fits use central-difference Jacobians,
as a model without an analytic one does in Curvis.
"""

from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from curvis.analysis.fit.engine import FitModel

type FloatArray = npt.NDArray[np.float64]
type Equation = Callable[[FloatArray, FloatArray], FloatArray]

PI = np.pi


def _rational3(x: FloatArray, b: FloatArray) -> FloatArray:
    return (b[0] + b[1] * x + b[2] * x**2 + b[3] * x**3) / (
        1 + b[4] * x + b[5] * x**2 + b[6] * x**3
    )


def _gauss(x: FloatArray, b: FloatArray) -> FloatArray:
    return (
        b[0] * np.exp(-b[1] * x)
        + b[2] * np.exp(-((x - b[3]) ** 2) / b[4] ** 2)
        + b[5] * np.exp(-((x - b[6]) ** 2) / b[7] ** 2)
    )


def _lanczos(x: FloatArray, b: FloatArray) -> FloatArray:
    return b[0] * np.exp(-b[1] * x) + b[2] * np.exp(-b[3] * x) + b[4] * np.exp(-b[5] * x)


def _chwirut(x: FloatArray, b: FloatArray) -> FloatArray:
    return np.exp(-b[0] * x) / (b[1] + b[2] * x)


def _exp_rise(x: FloatArray, b: FloatArray) -> FloatArray:
    return b[0] * (1 - np.exp(-b[1] * x))


EQUATIONS: dict[str, Equation] = {
    "Misra1a": _exp_rise,
    "Chwirut2": _chwirut,
    "Chwirut1": _chwirut,
    "Lanczos3": _lanczos,
    "Gauss1": _gauss,
    "Gauss2": _gauss,
    "DanWood": lambda x, b: b[0] * x ** b[1],
    "Misra1b": lambda x, b: b[0] * (1 - (1 + b[1] * x / 2) ** (-2)),
    "Kirby2": lambda x, b: (b[0] + b[1] * x + b[2] * x**2) / (1 + b[3] * x + b[4] * x**2),
    "Hahn1": _rational3,
    "MGH17": lambda x, b: b[0] + b[1] * np.exp(-x * b[3]) + b[2] * np.exp(-x * b[4]),
    "Lanczos1": _lanczos,
    "Lanczos2": _lanczos,
    "Gauss3": _gauss,
    "Misra1c": lambda x, b: b[0] * (1 - (1 + 2 * b[1] * x) ** (-0.5)),
    "Misra1d": lambda x, b: b[0] * b[1] * x * ((1 + b[1] * x) ** (-1)),
    "Roszman1": lambda x, b: b[0] - b[1] * x - np.arctan(b[2] / (x - b[3])) / PI,
    "ENSO": lambda x, b: (
        b[0]
        + b[1] * np.cos(2 * PI * x / 12)
        + b[2] * np.sin(2 * PI * x / 12)
        + b[4] * np.cos(2 * PI * x / b[3])
        + b[5] * np.sin(2 * PI * x / b[3])
        + b[7] * np.cos(2 * PI * x / b[6])
        + b[8] * np.sin(2 * PI * x / b[6])
    ),
    "MGH09": lambda x, b: b[0] * (x**2 + x * b[1]) / (x**2 + x * b[2] + b[3]),
    "Thurber": _rational3,
    "BoxBOD": _exp_rise,
    "Rat42": lambda x, b: b[0] / (1 + np.exp(b[1] - b[2] * x)),
    "MGH10": lambda x, b: b[0] * np.exp(b[1] / (x + b[2])),
    "Eckerle4": lambda x, b: (b[0] / b[1]) * np.exp(-0.5 * ((x - b[2]) / b[1]) ** 2),
    "Rat43": lambda x, b: b[0] / ((1 + np.exp(b[1] - b[2] * x)) ** (1 / b[3])),
    "Bennett5": lambda x, b: b[0] * (b[1] + x) ** (-1 / b[2]),
}


class NistModel(FitModel):
    def __init__(self, name: str, n_parameters: int) -> None:
        self.name = name
        self.parameters = tuple(f"b{i + 1}" for i in range(n_parameters))
        self._equation = EQUATIONS[name]

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        with np.errstate(all="ignore"):
            return np.asarray(self._equation(x, p), dtype=np.float64)
