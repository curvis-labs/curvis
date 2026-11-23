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
  or it converges where the data do not determine the parameters: every Y value is the
  same (with more than one free parameter), a parameter has no effect on the curve (its
  column of J is 0, or so small that changing the parameter by max(|value|, 1) moves
  the curve by less than 1e-12 times the RMS of Y, as for the LogEC50 of a phase whose
  span is ~1e-16), JᵀJ is numerically singular (reciprocal condition number of the
  column-scaled JᵀJ below 1e-12), or two parameters are correlated with |r| > 0.9999.
  Such a fit reports no parameters. (The R scripts reach the same verdicts on every
  fixture: there, `nlsLM` does not converge on constant data or on data without a
  minimum, or JᵀJ is singular.)
- **Automatic initial values.** Every model is linear in some of its parameters once
  the others are fixed. Curvis searches a grid of the nonlinear ones (K or Km on a log
  grid; Center × SD; pairs of LogEC50s at nine quantiles of X, slopes 1), solves the
  linear ones exactly at each grid point, and starts from the best; if that fit is not
  OK, it tries the next best, up to five starts.
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

## Equations and sketches

*About this analysis* in the Analyze dialog shows each model's equation and a sketch of
its curve (step 3.8).

- **Equations** are stored on the analysis type in the formula language
  (`CurveFit.equation`, e.g. `Y = Top / (1 + 10^((LogEC50 - X)*HillSlope))`) and rendered
  from the parsed formula (`curvis.formula.display`): − for minus, · for times, powers as
  superscripts, `_1` as a subscript. `tests/unit/test_analysis_about.py` compiles every
  equation with the formula compiler and checks that it equals the model's own function
  at perturbed example values, so the equation shown is the one that is fitted. The
  dose-response models add "where X is log10(concentration)".
- **Sketches** are computed, never drawn by hand: the model's own function at the
  example values below, at 100 points over the X range below (well under 5 ms). A fixed
  parameter's value replaces its example value, and its marker is drawn in the accent
  colour with its value ("Top = 100 (fixed)"). For the dose-response models the X range
  runs from 3 below the lowest LogEC50 to 3 above the highest, so a fixed LogEC50 stays
  in view. A marker whose position is not defined (the half-life for K ≤ 0) is left out.
  A model without markers gets the plain curve.

| Model | Example values | X range | Markers |
| --- | --- | --- | --- |
| Linear regression | Slope 1, Intercept 1 | 0 to 10 | dot at X = 0 ("Intercept") |
| Polynomial regression | degree 2: B0 1, B1 −1, B2 0.2; degree 3–6: roots evenly spaced over 1 to 9, scaled so that the largest \|Y\| on 0 to 10 is 10 | 0 to 10 | none |
| Hill 4PL | Bottom 0, Top 100, LogEC50 −7, Hill slope 1 | −10 to −4 | lines at Top and Bottom; line at EC50; "Hill slope: steepness at EC50" beside the curve at EC50 |
| 3PL | Top 100, LogEC50 −7, Hill slope 1 | −10 to −4 | line at Top; line at EC50; Hill slope label |
| Biphasic | Bottom 0, Top 100, Frac 0.4, LogEC50 −8 and −5, Hill slopes 1 | −11 to −2 | lines at Top and Bottom; lines at EC50 1 and EC50 2 |
| Bell-shaped | Plateau 1 0, Dip 100, Plateau 2 10, LogEC50 −8 and −5, Hill slopes 1.5 | −11 to −2 | line at Dip; lines at EC50 1 and EC50 2 |
| Exponential association | Y0 0, Plateau 100, K 0.3 | 0 to 20 | line at Plateau; line at the half-time (ln 2 / K) |
| Exponential decay | Y0 100, Plateau 10, K 0.3 | 0 to 20 | line at Plateau; line at the half-life (ln 2 / K) |
| Michaelis-Menten | Vmax 100, Km 2 | 0 to 20 | line at Vmax; line at Km (up to the curve, at Vmax / 2) |
| Gaussian | Baseline 5, Amplitude 100, Center 0, SD 1 | −4 to 4 | line at Baseline; line at Center; "Amplitude" beside the peak |

The example values are also what the Fix table fills in when *Fix* is ticked.

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
