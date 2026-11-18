# Tolerance exceptions

Curvis results are compared with R reference fixtures at the tolerances in CLAUDE.md:
test statistics and p-values 1e-8 relative, fitted parameters 1e-6, confidence
intervals 1e-4, counts exactly (`tests/validation/tolerances.py`).

An exception to these tolerances needs the project owner's approval **before** it is
added. Each exception is listed below with its key, exactly as in `EXCEPTIONS` in
`tests/validation/tolerances.py` (for example dunnett/*:p_value, in backticks), the tolerance,
the reason, and who approved it and when. `test_fixtures.py` fails if a key in
`EXCEPTIONS` is not listed here.

| Key | Tolerance | Reason | Approved |
| --- | --- | --- | --- |
| `biphasic/noisy_04:bottom` | 1e-5 | Flat minimum (see below) | Project owner, 2026-11-18 (step 3.3) |
| `biphasic/noisy_04:bottom_se` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |
| `biphasic/noisy_04:ec50_2` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |
| `biphasic/noisy_04:band_fit` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |
| `exp_association/noisy_09:y0` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |
| `exp_association/noisy_09:band_fit` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |

**Flat minimum.** In both cases the least-squares minimum is so flat that double
precision determines these outputs only to ~1e-6 relative: Curvis and R end at points
whose RSS differs at rounding level (~1e-17 relative), both with the residuals
orthogonal to the Jacobian (cosine < 1e-7), and Curvis differs from R by 1.4e-6 to
3.1e-6. biphasic/noisy_04 is poorly determined (nH2 ≈ 0.25, the CI of EC50_2 reaches
2e-16), and R itself is not reproducible there at 1e-6: R 4.3.3 on Linux and R 4.6.1
on Windows differ by up to 1.3e-6. In exp_association/noisy_09 (Y0 ≈ 99 for data from
1e3 to 4e4) the two R runs agree, but the RSS at R's and at Curvis' Y0 differs by
3e-8 out of 2.2e8, which is the rounding level of the RSS.

## Outputs left out of a fixture

Not tolerance exceptions: outputs that cannot be reproduced in double precision are left
out of the fixture by its R script, with the reason in the script and the fixture's
description, and the matching adapter in `adapters.py` leaves them out too.

| Fixtures | Left out | Reason | Approved |
| --- | --- | --- | --- |
| `nist_linear/wampler1`, `nist_linear/wampler2` | `se`, `sy_x` | Exact fits: the certified SDs are 0, and a relative comparison would need exact zeros. | Step 2.4 |
| `nist_nls/lanczos1_*` | `se`, `rss`, `sy_x` | Residuals ~1e-13, so the RSS (~1e-25) is at rounding level: evaluating the model in double precision changes it by ~0.2% and the SDs by ~0.1%. The parameters match NIST to 1e-6 and are compared. | Project owner, 2026-11-04 (step 2.4) |
