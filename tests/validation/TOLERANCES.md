# Tolerance exceptions

Curvis results are compared with R reference fixtures at the tolerances in CLAUDE.md:
test statistics and p-values 1e-8 relative, fitted parameters 1e-6, confidence
intervals 1e-4, counts exactly (`tests/validation/tolerances.py`).

An exception to these tolerances needs the project owner's approval **before** it is
added. Each exception is listed below with its key, exactly as in `EXCEPTIONS` in
`tests/validation/tolerances.py` (for example dunnett/*:p_value, in backticks), the tolerance,
the reason, and who approved it and when. `test_fixtures.py` fails if a key in
`EXCEPTIONS` is not listed here.

There are no exceptions yet.
