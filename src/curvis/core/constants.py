"""Project constants: named numbers that formulas in this project can use."""

import math
from dataclasses import dataclass, field

from curvis.data import new_id

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
"""A plain JSON value, used for per-project settings."""


@dataclass
class ProjectConstant:
    """A user constant saved in the project, e.g. ``k_on = 1.2e5 1/(M*s)``."""

    name: str
    value: float
    unit: str = ""
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.value = float(self.value)
        if not math.isfinite(self.value):
            raise ValueError(f"constant '{self.name}' must be a finite number")
