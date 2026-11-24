"""Project file format (.cvp), recovery copies, CSV/Excel import and Prism import.

The names below come from :mod:`curvis.io.project`, which is imported on first use: it
loads pyarrow, which start-up must not wait for (:mod:`curvis.io.recovery` is needed at
start-up and imports this package).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from curvis.io.project import (
        SCHEMA_VERSION,
        CorruptProjectError,
        NewerVersionError,
        ProjectFileError,
        load,
        save,
    )

__all__ = [
    "SCHEMA_VERSION",
    "CorruptProjectError",
    "NewerVersionError",
    "ProjectFileError",
    "load",
    "save",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from curvis.io import project

        return getattr(project, name)
    raise AttributeError(f"module 'curvis.io' has no attribute {name!r}")
