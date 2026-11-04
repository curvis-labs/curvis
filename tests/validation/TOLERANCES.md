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

## Outputs left out of a fixture

Not tolerance exceptions: outputs that cannot be reproduced in double precision are left
out of the fixture by its R script, with the reason in the script and the fixture's
description, and the matching adapter in `adapters.py` leaves them out too.

| Fixtures | Left out | Reason | Approved |
| --- | --- | --- | --- |
| `nist_linear/wampler1`, `nist_linear/wampler2` | `se`, `sy_x` | Exact fits: the certified SDs are 0, and a relative comparison would need exact zeros. | Step 2.4 |
| `nist_nls/lanczos1_*` | `se`, `rss`, `sy_x` | Residuals ~1e-13, so the RSS (~1e-25) is at rounding level: evaluating the model in double precision changes it by ~0.2% and the SDs by ~0.1%. The parameters match NIST to 1e-6 and are compared. | Project owner, 2026-11-04 (step 2.4) |
