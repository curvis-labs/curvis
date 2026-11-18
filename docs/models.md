# Curve-fit models

The exact equation of every nonlinear model Curvis fits, the names and meanings of
its parameters, the form in which they are reported, and the values derived from
them. The R scripts that produce the validation fixtures (`validation/r/scripts/`)
fit the same equations with `minpack.lm::nlsLM`.

## Common definitions

These apply to every model below (and to the 4PL and 3PL,
`validation/r/scripts/dose_response.R`).

- **Points.** Every replicate value is its own point, paired with the X of its row. A
  point with a missing X or Y is left out, and so is a point outside the X range (given
  in the units of the X column).
- **Log X.** The dose-response models are written on X′ = log10(concentration). When
  *X values are log10(concentration)* is ticked, X′ = X. Otherwise X = 0 is allowed
  (X′ = −∞; the curve takes its limit there) and a negative concentration is left out.
  EC50 = 10^LogEC50.
- **Fit.** Unweighted least squares (Levenberg-Marquardt). Every parameter is
  unbounded unless the user fixes it.
- **Standard errors.** Asymptotic: SE = sqrt(diag(s² (JᵀJ)⁻¹)), with J the Jacobian of
  the model in the free parameters at the best fit, s² = RSS / df and
  df = n − number of free parameters.
- **Confidence intervals** (95%). A parameter: estimate ± t(0.975, df) · SE.
  A value that is a linear combination cᵀp of the parameters: SE = sqrt(cᵀ V c), with
  V = s² (JᵀJ)⁻¹, and the same ± t · SE. A value that is a monotone function of one
  parameter (EC50 = 10^LogEC50, half-life = ln 2 / K): the same function of the
  parameter's CI limits, sorted.
- **Goodness of fit.** R² = 1 − RSS / TSS; Sy.x = sqrt(RSS / df).
- **Confidence band.** Fitted value ± t(0.975, df) · sqrt(g V gᵀ), with g the gradient of
  the model in the free parameters at that X (the delta method).
- **No fit.** There are fewer points than free parameters; the fit does not converge;
  or it converges where the data do not determine the parameters: a parameter has no
  effect on the curve, JᵀJ is numerically singular (reciprocal condition number of the
  column-scaled JᵀJ below 1e-12), or two parameters are correlated with |r| > 0.9999.
  Such a fit reports no parameters.
- **df = 0** (as many points as free parameters): the curve goes through every point;
  the parameters are reported, but there is no SE, CI, Sy.x or band.

## Dose-response

### Biphasic dose-response

```
Y = Bottom + (Top − Bottom) · [      Frac  / (1 + 10^((LogEC50_1 − X′) · nH1))
                               + (1 − Frac) / (1 + 10^((LogEC50_2 − X′) · nH2)) ]
```

| Parameter | Meaning |
| --- | --- |
| Bottom, Top | The plateaus at the two ends of the curve (Y units) |
| Frac | The fraction of the span (Top − Bottom) in the first phase |
| LogEC50_1, LogEC50_2 | log10 of the concentrations at the middle of each phase |
| nH1, nH2 | Hill slopes of the two phases; positive rises, negative falls |

**Reported form.** The same curve can be written in other ways; Curvis reports one, and
only changes parameters that are free:

1. Top ≥ Bottom. If Top < Bottom, Top and Bottom are swapped and both Hill slopes
   negated (needs Bottom, Top and both slopes free).
2. LogEC50_1 ≤ LogEC50_2. Otherwise the two phases (LogEC50 and Hill slope) are swapped
   and Frac becomes 1 − Frac (needs Frac and both LogEC50s free, and the two slopes
   either both free or both fixed to the same value).

**Derived.** EC50_1 = 10^LogEC50_1 and EC50_2 = 10^LogEC50_2, each with CI 10^(CI of the
LogEC50).

### Bell-shaped dose-response

```
Y = Dip + (Plateau1 − Dip) / (1 + 10^((X′ − LogEC50_1) · nH1))
        + (Plateau2 − Dip) / (1 + 10^((LogEC50_2 − X′) · nH2))
```

| Parameter | Meaning |
| --- | --- |
| Plateau1 | Y at low concentrations |
| Dip | Y between the two transitions: the top of the bell, or the bottom of a U |
| Plateau2 | Y at high concentrations |
| LogEC50_1, LogEC50_2 | log10 of the concentrations at the middle of the first and second transitions |
| nH1, nH2 | Hill slopes of the two transitions |

With positive slopes and LogEC50_1 < LogEC50_2, Y goes from Plateau1 to Dip and then to
Plateau2. There is no second way of writing the same curve, so the parameters are
reported as fitted.

**Derived** (each linear in the parameters, so its SE is exact under the asymptotic
covariance: SE = sqrt(cᵀ V c)):

| Value | Definition | Units |
| --- | --- | --- |
| Peak center | (LogEC50_1 + LogEC50_2) / 2 | log10 concentration |
| Peak width | LogEC50_2 − LogEC50_1 | log10 units |
| Peak amplitude | Dip − (Plateau1 + Plateau2) / 2 | Y units; negative for a U |
| EC50_1, EC50_2 | 10^LogEC50_1, 10^LogEC50_2, CI 10^(CI of the LogEC50) | concentration |

A derived value is reported only when every parameter it uses is free.

## Kinetics

### One-phase exponential association

```
Y = Y0 + (Plateau − Y0) · (1 − exp(−K · X))
```

### One-phase exponential decay

```
Y = Plateau + (Y0 − Plateau) · exp(−K · X)
```

The two equations describe the same family of curves (multiply out the first); they
differ in the data they are meant for, the sign of the span and the wording of the
results.

| Parameter | Meaning |
| --- | --- |
| Y0 | Y at X = 0 |
| Plateau | Y as X → ∞ (for K > 0) |
| K | Rate constant, in 1 / (X units). Unbounded: K < 0 is a curve that grows away from the plateau, and Curvis warns about it |

**Derived.**

| Value | Association | Decay | CI |
| --- | --- | --- | --- |
| Span | Plateau − Y0 | Y0 − Plateau | ± t · SE, SE = sqrt(V_Y0 + V_Plateau − 2 V_Y0,Plateau) |
| Half-time (association) / half-life (decay) | ln 2 / K | ln 2 / K | ln 2 / (CI of K) |
| Tau (time constant) | 1 / K | 1 / K | 1 / (CI of K) |

Half-time, half-life and tau are reported only for K > 0. When the lower CI limit of K
is ≤ 0, the upper limit of their CI is ∞.

### Michaelis-Menten

```
Y = Vmax · X / (Km + X)
```

| Parameter | Meaning |
| --- | --- |
| Vmax | Maximum velocity (Y units) |
| Km | Michaelis constant: the substrate concentration at half of Vmax (X units) |

X is the substrate concentration and Y the initial velocity. Both parameters are
unbounded.

## Shape-based

### Gaussian

```
Y = Baseline + Amplitude · exp(−½ · ((X − Center) / SD)²)
```

| Parameter | Meaning |
| --- | --- |
| Baseline | Y far from the peak |
| Amplitude | Height of the peak above the baseline; negative for a dip |
| Center | X of the peak |
| SD | Width of the peak (X units); reported as a positive number, since SD and −SD give the same curve |

**Derived.** FWHM (full width at half maximum) = 2 · sqrt(2 ln 2) · SD ≈ 2.3548 · SD,
with SE = 2 · sqrt(2 ln 2) · SE(SD) and CI ± t · SE.

Fixing Baseline at 0 gives the plain Gaussian, Y = Amplitude · exp(−½ ((X − Center) / SD)²).

## Validation

| Model | Fixtures | R script | Other checks |
| --- | --- | --- | --- |
| Biphasic | `tests/validation/fixtures/biphasic/` | `biphasic.R` | — |
| Bell-shaped | `bell_shaped/` | `bell_shaped.R` | — |
| Exponential association | `exp_association/` | `exp_association.R` | `stats::nls` with `SSasymp`; NIST Misra1a, BoxBOD (Y0 fixed at 0: b1 = Plateau, b2 = K) |
| Exponential decay | `exp_decay/` | `exp_decay.R` | `stats::nls` with `SSasymp` |
| Michaelis-Menten | `michaelis_menten/` | `michaelis_menten.R` | `stats::nls` with `SSmicmen`; NIST Misra1d (Vmax = b1, Km = 1 / b2) |
| Gaussian | `gaussian/` | `gaussian.R` | — |

The scripts share `validation/r/R/nonlinear.R`. Each fit is started from the true
parameters and from 24 random perturbations of them, so the reference does not depend
on Curvis' automatic initial values; the lowest RSS wins, Gauss-Newton steps finish
it, and the script checks that it is a minimum (the residuals are orthogonal to every
column of J, cosine < 1e-7) and that its SEs equal `nlsLM`'s to 1e-3. Every model has
a low-noise case, ten cases with realistic noise, a case without a minimum (its
parameters run off to infinity, or are not determined), and cases with two points,
ties, missing values with unbalanced replicates, constant data, an X range and, where
it applies, fixed parameters, log10 X and a 0 control.
