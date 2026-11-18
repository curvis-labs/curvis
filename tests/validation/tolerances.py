"""Tolerances for comparing Curvis results with R reference fixtures (CLAUDE.md).

Each output in a fixture has a *kind* that selects its relative tolerance. Exceptions
are allowed only when they are listed, with a reason and the user's approval, in
tests/validation/TOLERANCES.md; ``test_fixtures.py`` checks that every key in
``EXCEPTIONS`` appears there.
"""

from fnmatch import fnmatchcase

# Relative tolerance per kind. "exact" values (counts, degrees of freedom) must be equal.
TOLERANCES: dict[str, float] = {
    "exact": 0.0,
    "statistic": 1e-8,
    "p_value": 1e-8,
    "parameter": 1e-6,
    "ci": 1e-4,
}

# "<analysis>/<case>:<output>" (shell-style wildcards allowed) -> relative tolerance.
# Example: "dunnett/*:p_value": 1e-5. Each key must appear in TOLERANCES.md.
EXCEPTIONS: dict[str, float] = {
    # Flat least-squares minima: double precision fixes these only to ~1e-6 (step 3.3).
    "biphasic/noisy_04:bottom": 1e-5,
    "biphasic/noisy_04:bottom_se": 1e-5,
    "biphasic/noisy_04:ec50_2": 1e-5,
    "biphasic/noisy_04:band_fit": 1e-5,
    "exp_association/noisy_09:y0": 1e-5,
    "exp_association/noisy_09:band_fit": 1e-5,
}


def tolerance_for(fixture_id: str, output: str, kind: str) -> tuple[float, str]:
    """The relative tolerance for one output, and where it comes from (for reports)."""
    for key, tolerance in EXCEPTIONS.items():
        if fnmatchcase(f"{fixture_id}:{output}", key):
            return tolerance, f"exception {key}"
    return TOLERANCES[kind], kind
