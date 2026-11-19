# Tolerances and exceptions

Curvis results are compared with R reference fixtures at the tolerances in CLAUDE.md:
test statistics and p-values 1e-8 relative, fitted parameters 1e-6, confidence
intervals 1e-4, counts exactly (`tests/validation/tolerances.py`).

## Parameters smaller than their standard error

A fitted parameter whose fixture also holds its standard error (kind `parameter`, with
an output `<name>_se`) is compared on the larger of its value and its SE:
`|actual − expected| ≤ 1e-6 × max(|expected|, SE)`, with R's SE (`compare()` in
`tests/validation/harness.py`). For a parameter estimate larger than its SE this is the
plain relative tolerance. An estimate smaller than its SE is statistically
indistinguishable from 0, and double precision fixes it only to about 1e-6 of the SE,
not of the value: Curvis and R end at points whose RSS differs at rounding level, and
the math libraries of Linux, macOS and Windows (and of different runner machines) move
such a value by up to ~1.3e-6 relative. Examples from step 3.3, all with every other
output of the fit matching R to 1e-9–1e-12:

| Output | Value | SE | Largest difference seen |
| --- | --- | --- | --- |
| `biphasic/noisy_10:bottom` | 0.105 | 2.04 | 1.16e-6 relative (macOS, Windows) |
| `bell_shaped/noisy_07:plateau_1` | −0.0107 | 1.44 | 1.34e-6 relative (Windows) |
| `biphasic/noisy_04:bottom` | −7.4 | 49.5 | 1.4e-6 relative |
| `exp_association/noisy_09:y0` | 98.7 | 2,120 | 1.5e-6 relative |

38 parameters of the step 3.3 fixtures are smaller than their SE. The rule applies only
to parameters: their SEs, derived values and fitted curve values keep the plain relative
tolerance. Approved by the project owner, 2026-11-19 (step 3.3, PR #41); it replaces the
exceptions for `biphasic/noisy_04:bottom` and `exp_association/noisy_09:y0`.

## Exceptions

An exception to these tolerances needs the project owner's approval **before** it is
added. Each exception is listed below with its key, exactly as in `EXCEPTIONS` in
`tests/validation/tolerances.py` (for example dunnett/*:p_value, in backticks), the tolerance,
the reason, and who approved it and when. `test_fixtures.py` fails if a key in
`EXCEPTIONS` is not listed here.

| Key | Tolerance | Reason | Approved |
| --- | --- | --- | --- |
| `biphasic/noisy_04:bottom_se` | 1e-5 | Flat minimum (see below) | Project owner, 2026-11-18 (step 3.3) |
| `biphasic/noisy_04:ec50_2` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |
| `biphasic/noisy_04:band_fit` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |
| `exp_association/noisy_09:band_fit` | 1e-5 | Flat minimum | Project owner, 2026-11-18 (step 3.3) |

**Flat minimum.** In both cases the least-squares minimum is so flat that double
precision determines these outputs only to ~1e-6 relative: Curvis and R end at points
whose RSS differs at rounding level (~1e-17 relative), both with the residuals
orthogonal to the Jacobian (cosine < 1e-7), and Curvis differs from R by 1.0e-6 to
3.1e-6 on these outputs. (Their Bottom and Y0, at 1.4e-6 and 1.5e-6, are within the SE
rule above.) biphasic/noisy_04 is poorly determined (nH2 ≈ 0.25, the CI of EC50_2 reaches
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
