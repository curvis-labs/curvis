"""Analysis framework: definitions, analysis types, parameters and the registry.

- :class:`Analysis` is what a document stores: the analysis type, its source table,
  the selected inputs and a parameters dataclass. It holds no results; those are
  derived (rule 2 of CLAUDE.md) and recomputed by the analysis engine.
- :class:`AnalysisType` is one kind of analysis (t-test, 4PL fit …). It says whether a
  table can support it (:meth:`~AnalysisType.can_run`), copies its inputs out of the
  table on the main thread (:meth:`~AnalysisType.gather`) and computes a
  :class:`~curvis.analysis.result.Result` with :meth:`~AnalysisType.run`, a pure
  function that runs in the scheduler.
- Parameters are a frozen dataclass per type. :func:`param` describes each field (its
  label, whether it is an advanced option, its choices), so the Analyze dialog can
  build the form, and :func:`params_to_json` / :func:`params_from_json` store it.
- The registry lists the types. Built-in types load on first use, not at start-up.

No Qt here: this module is imported by the document model.
"""

from __future__ import annotations

import dataclasses
import importlib
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import numpy as np
import numpy.typing as npt

from curvis.analysis.result import Result
from curvis.data import ColumnRole, Pairing, Table, new_id, series

if TYPE_CHECKING:  # curvis.core imports this module
    from curvis.core.constants import JsonValue

type FloatArray = npt.NDArray[np.float64]
type Weight = Literal["light", "heavy"]

BUILTIN_MODULES = (
    "curvis.analysis.descriptive",
    "curvis.analysis.compare.ttest_analysis",
    "curvis.analysis.compare.anova_analysis",
    "curvis.analysis.compare.mann_whitney_analysis",
    "curvis.analysis.fit.analyses",
)
"""Modules that register the built-in analysis types when imported."""


class Family(StrEnum):
    """The families of the Analyze dialog, in the order of its tabs."""

    COMPARE = "compare"
    FIT = "fit"
    SURVIVAL = "survival"

    @property
    def label(self) -> str:
        return _FAMILY_LABELS[self]

    @property
    def description(self) -> str:
        return _FAMILY_DESCRIPTIONS[self]


_FAMILY_LABELS = {
    Family.COMPARE: "Compare groups",
    Family.FIT: "Fit a curve",
    Family.SURVIVAL: "Survival",
}

_FAMILY_DESCRIPTIONS = {
    Family.COMPARE: "Describe groups, or test whether their means or medians differ.",
    Family.FIT: "Fit a line or a curve to XY data.",
    Family.SURVIVAL: "Estimate survival curves from follow-up times and events.",
}


class AnalysisError(Exception):
    """An analysis cannot run on its inputs. The message says what to change."""


# --- parameters ------------------------------------------------------------------------


@dataclass(frozen=True)
class Params:
    """Base class of the parameters dataclass of each analysis type."""


@dataclass(frozen=True)
class ParamInfo:
    """How the Analyze dialog shows one parameter."""

    label: str
    """Form label or check box text, with a mnemonic (&)."""
    advanced: bool = False
    """Shown in the collapsed Advanced section."""
    choices: tuple[tuple[str, str], ...] = ()
    """(value, label) pairs for a text parameter picked from a list."""
    minimum: float | None = None
    maximum: float | None = None
    from_inputs: bool = False
    """A text parameter picked from the ticked inputs (e.g. a control group): the value
    is an input id, and "" means the first ticked input."""
    enabled_when: tuple[str, str] | None = None
    """(other parameter, value): the field is enabled only while the other parameter has
    that value."""


PARAM_INFO = "curvis.param"


def param(
    default: bool | int | float | str,
    label: str,
    *,
    advanced: bool = False,
    choices: Sequence[tuple[str, str]] = (),
    minimum: float | None = None,
    maximum: float | None = None,
    from_inputs: bool = False,
    enabled_when: tuple[str, str] | None = None,
) -> Any:
    """A parameters dataclass field with its default and how the dialog shows it."""
    if from_inputs and (not isinstance(default, str) or choices):
        raise TypeError("a parameter picked from the inputs is text without fixed choices")
    info = ParamInfo(label, advanced, tuple(choices), minimum, maximum, from_inputs, enabled_when)
    return field(default=default, metadata={PARAM_INFO: info})


def input_param(params: Params, name: str, inputs: Sequence[str]) -> str | None:
    """The input id that parameter ``name`` (declared with ``from_inputs``) selects among
    ``inputs``: its value, or the first input when the value is "" or not among them."""
    value = getattr(params, name)
    if value in inputs:
        return str(value)
    return inputs[0] if inputs else None


def param_fields(params_type: type[Params]) -> list[tuple[dataclasses.Field[Any], ParamInfo]]:
    """The fields of a parameters dataclass with their :class:`ParamInfo`, in order."""
    found: list[tuple[dataclasses.Field[Any], ParamInfo]] = []
    for f in dataclasses.fields(params_type):
        info = f.metadata.get(PARAM_INFO)
        if not isinstance(info, ParamInfo):
            raise TypeError(f"{params_type.__name__}.{f.name} is not declared with param()")
        found.append((f, info))
    return found


def params_to_json(params: Params) -> dict[str, JsonValue]:
    """The parameters as a JSON object."""
    return {f.name: getattr(params, f.name) for f, _info in param_fields(type(params))}


def params_from_json[P: Params](params_type: type[P], data: dict[str, Any]) -> P:
    """Parameters from a JSON object. Missing keys keep their default, unknown keys are
    ignored, and a value of the wrong type or out of range raises ``ValueError``."""
    values: dict[str, Any] = {}
    for f, info in param_fields(params_type):
        if f.name not in data:
            continue
        values[f.name] = check_param(f, info, data[f.name])
    return params_type(**values)


def check_param(f: dataclasses.Field[Any], info: ParamInfo, value: Any) -> Any:
    """``value`` checked against the type and limits of parameter ``f``."""
    default = f.default
    name = f.name
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"parameter {name} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"parameter {name} must be a whole number")
    elif isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"parameter {name} must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"parameter {name} must be finite")
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError(f"parameter {name} must be text")
        if info.choices and value not in {v for v, _ in info.choices}:
            raise ValueError(f"parameter {name} cannot be {value!r}")
        return value
    if info.minimum is not None and value < info.minimum:
        raise ValueError(f"parameter {name} must be at least {info.minimum:g}")
    if info.maximum is not None and value > info.maximum:
        raise ValueError(f"parameter {name} must be at most {info.maximum:g}")
    return value


# --- definitions and inputs --------------------------------------------------------------


@dataclass(frozen=True)
class Analysis:
    """An analysis as the document stores it: type, source table, inputs and parameters.

    ``inputs`` are the selected input ids, in table order (for series: the replicate
    group id, or the column id of a Y column without a group). Analyses are replaced as a
    whole when they change, so a command can undo a change by putting the old one back.
    """

    type: str
    table_id: str
    inputs: tuple[str, ...]
    params: Params
    pairing: Pairing | None = None
    """For paired analyses (step 2.3); unused so far."""
    id: str = field(default_factory=new_id)

    @property
    def analysis_type(self) -> AnalysisType:
        return get(self.type)


@dataclass(frozen=True)
class InputChoice:
    """Something an analysis can read, as listed in the Analyze dialog."""

    id: str
    name: str
    n_values: int
    """Number of values that are not missing."""
    column_ids: tuple[str, ...]

    @property
    def n_replicates(self) -> int:
        """Number of replicate columns (1 for a Y column without a group)."""
        return len(self.column_ids)

    def n(self, rows_match_x: bool) -> int:
        """The n shown for this input: its replicates when the rows are X values (each
        row is one X, so the number of values says nothing), else its number of values."""
        return self.n_replicates if rows_match_x else self.n_values


@dataclass(frozen=True)
class SeriesData:
    """The values of one selected series, copied out of the table."""

    id: str
    name: str
    columns: tuple[FloatArray, ...]
    """One array per replicate column, all of the table's length."""


@dataclass(frozen=True)
class AnalysisInputs:
    """Everything :meth:`AnalysisType.run` reads: plain copies, safe to use off the main thread."""

    table_name: str
    n_rows: int
    series: tuple[SeriesData, ...]
    x: FloatArray | None = None
    x_name: str = ""


def series_choices(table: Table) -> list[InputChoice]:
    """The table's series (replicate groups and ungrouped Y columns) with their n."""
    choices: list[InputChoice] = []
    for s in series(table):
        n = sum(int(np.count_nonzero(~np.isnan(table.column(c).values))) for c in s.column_ids)
        choices.append(InputChoice(s.group_id or s.column_ids[0], s.name, n, s.column_ids))
    return choices


def rows_match_x(table: Table) -> bool:
    """True when the rows are matched to numeric X values: the table has an X column
    with at least one value. A table without one (or with rows labelled by text) holds
    column data, where each series is a sample of values."""
    x = x_column_id(table)
    return x is not None and bool(np.any(~np.isnan(table.column(x).values)))


def x_column_id(table: Table) -> str | None:
    """The first X column of ``table``, or None."""
    return next((c.id for c in table.columns if c.role is ColumnRole.X), None)


# --- analysis types --------------------------------------------------------------------


class AnalysisType(ABC):
    """One kind of analysis. Subclasses set the class attributes and implement
    :meth:`describe` and :meth:`run`."""

    id: ClassVar[str]
    """Stable id, stored in project files and settings."""
    label: ClassVar[str]
    """Name in the Analyze dialog, with a mnemonic."""
    method: ClassVar[str]
    """Exact method name shown with the results."""
    family: ClassVar[Family]
    params_type: ClassVar[type[Params]]
    weight: ClassVar[Weight] = "light"
    """Heavy analyses (many fits) run in a worker process."""
    uses_x: ClassVar[bool] = False
    """Reads the table's X column (it is then a dependency too)."""
    imports: ClassVar[tuple[str, ...]] = ()
    """Heavy modules :meth:`run` imports lazily; the scheduler loads them on the main
    thread first (see :mod:`curvis.compute.jobs`)."""

    @property
    def name(self) -> str:
        """:attr:`label` without the mnemonic marker."""
        return self.label.replace("&&", "\0").replace("&", "").replace("\0", "&")

    def candidates(self, table: Table) -> list[InputChoice]:
        """What this analysis can read from ``table`` (default: its series)."""
        return series_choices(table)

    def problem(self, table: Table) -> str | None:
        """Why ``table`` cannot support this analysis, or None."""
        if not self.candidates(table):
            return "Needs at least one Y column."
        return None

    def can_run(self, table: Table) -> bool:
        """True if ``table`` has the data this analysis needs."""
        return self.problem(table) is None

    def default_inputs(self, table: Table) -> tuple[str, ...]:
        """Inputs ticked when the dialog opens (default: all candidates)."""
        return tuple(c.id for c in self.candidates(table))

    def default_params(self) -> Params:
        return self.params_type()

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        """Why Run must stay disabled for this choice, or None (one short sentence)."""
        if not inputs:
            return "Tick at least one series."
        return None

    @abstractmethod
    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        """One plain sentence that says what will run, with exact method names."""

    def selected(self, table: Table, inputs: Sequence[str]) -> list[InputChoice]:
        """The candidates in ``inputs``, in table order."""
        wanted = set(inputs)
        return [c for c in self.candidates(table) if c.id in wanted]

    def reads(self, table: Table, analysis: Analysis) -> list[str]:
        """Column ids the analysis reads (its dependency-graph inputs)."""
        reads = [c for choice in self.selected(table, analysis.inputs) for c in choice.column_ids]
        x = x_column_id(table) if self.uses_x else None
        if x is not None:
            reads.append(x)
        return list(dict.fromkeys(reads))

    def gather(self, table: Table, analysis: Analysis) -> AnalysisInputs:
        """Copy the inputs out of ``table`` (main thread). Raises :class:`AnalysisError`."""
        choices = self.selected(table, analysis.inputs)
        if len(choices) != len(set(analysis.inputs)):
            raise AnalysisError(
                "Some of the data this analysis used is gone. Click Edit… to choose it again."
            )
        data = tuple(
            SeriesData(
                c.id,
                c.name,
                tuple(np.array(table.column(i).values, dtype=np.float64) for i in c.column_ids),
            )
            for c in choices
        )
        x_id = x_column_id(table) if self.uses_x else None
        x = np.array(table.column(x_id).values, dtype=np.float64) if x_id else None
        x_name = table.column(x_id).name if x_id else ""
        return AnalysisInputs(table.name, table.n_rows, data, x, x_name)

    @staticmethod
    @abstractmethod
    def run(inputs: AnalysisInputs, params: Any) -> Result:
        """Compute the result. Pure: reads only its arguments; runs in the scheduler.

        Raises :class:`AnalysisError` with what to change when it cannot run.
        """


# --- registry --------------------------------------------------------------------------

_TYPES: dict[str, AnalysisType] = {}
_builtin_loaded = False


def register(analysis_type: AnalysisType) -> AnalysisType:
    """Add an analysis type to the registry."""
    if analysis_type.id in _TYPES:
        raise ValueError(f"An analysis type with id {analysis_type.id!r} is already registered.")
    param_fields(analysis_type.params_type)  # every field must be declared with param()
    _TYPES[analysis_type.id] = analysis_type
    return analysis_type


def unregister(type_id: str) -> None:
    """Remove an analysis type (for tests)."""
    _TYPES.pop(type_id, None)


def load_builtin() -> None:
    """Import the modules of the built-in analyses (once)."""
    global _builtin_loaded
    if _builtin_loaded:
        return
    _builtin_loaded = True
    for module in BUILTIN_MODULES:
        importlib.import_module(module)


def get(type_id: str) -> AnalysisType:
    """The analysis type with ``type_id``; raises ``KeyError`` if there is none."""
    load_builtin()
    return _TYPES[type_id]


def types() -> list[AnalysisType]:
    """All analysis types: by family, then built-in types in the order of
    BUILTIN_MODULES (the order of SPEC.md), then others in registration order."""
    load_builtin()
    families = list(Family)
    registered = list(_TYPES)

    def key(t: AnalysisType) -> tuple[int, int, int]:
        module = type(t).__module__
        builtin = (
            BUILTIN_MODULES.index(module) if module in BUILTIN_MODULES else len(BUILTIN_MODULES)
        )
        return families.index(t.family), builtin, registered.index(t.id)

    return sorted(_TYPES.values(), key=key)


def in_family(family: Family) -> list[AnalysisType]:
    return [t for t in types() if t.family is family]


def runnable(table: Table, family: Family | None = None) -> list[AnalysisType]:
    """Analysis types that ``table`` can support (in ``family``, if given)."""
    return [t for t in types() if (family is None or t.family is family) and t.can_run(table)]


def available_families(table: Table) -> list[Family]:
    """Families with at least one analysis ``table`` can support, in tab order."""
    usable = {t.family for t in runnable(table)}
    return [f for f in Family if f in usable]


def preferred_family(table: Table) -> Family:
    """The family that best fits the data (SPEC.md): survival when there are time/event
    pairs, curve fits when there are XY pairs, group comparisons otherwise."""
    roles = {c.role for c in table.columns}
    if ColumnRole.TIME in roles and ColumnRole.EVENT in roles:
        return Family.SURVIVAL
    if ColumnRole.X in roles and ColumnRole.Y in roles:
        return Family.FIT
    return Family.COMPARE


def opening_family(table: Table) -> Family | None:
    """The family the Analyze dialog opens on: the preferred one if it has analyses for
    this table, else the first family that has; None if the table supports nothing."""
    families = available_families(table)
    preferred = preferred_family(table)
    if preferred in families:
        return preferred
    return families[0] if families else None
