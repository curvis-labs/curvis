# Gaussian peak with a baseline, with each parameter's SE and 95% CI, the full width at
# half maximum (FWHM) with its SE and CI, R², Sy.x and the 95% confidence band.
#
# Curvis: curvis.analysis.fit.shape. Equation and definitions: docs/models.md.
#
#   Y = Baseline + Amplitude * exp(-0.5 * ((X - Center) / SD)^2)
#
# Baseline (Y far from the peak), Amplitude (height of the peak above the baseline;
# negative for a dip), Center (X of the peak) and SD (width; reported as |SD|, since
# SD and -SD give the same curve). Derived: FWHM = 2 sqrt(2 ln 2) SD, linear in SD:
# SE = 2 sqrt(2 ln 2) SE(SD), CI -/+ t * SE. "Baseline fixed at 0" gives the plain
# Gaussian Y = Amplitude * exp(-0.5 * ((X - Center) / SD)^2).
# Fit, SE, CI, band and the no-fit rules: validation/r/R/nonlinear.R.
# No other R implementation of this model to cross-check against; the NIST StRD Gauss1-3
# datasets (two peaks on an exponential baseline) are a different model.

source(file.path(r_project_dir(), "R", "nonlinear.R"), local = TRUE)

FWHM_FACTOR <- 2 * sqrt(2 * log(2))

model <- list(
  name = "gaussian",
  params = c("baseline", "amplitude", "center", "sd"),
  log_x = FALSE,
  f = function(p, x) p[["baseline"]] + p[["amplitude"]] * exp(-0.5 * ((x - p[["center"]]) / p[["sd"]])^2),
  grad = function(p, x) {
    u <- (x - p[["center"]]) / p[["sd"]]
    e <- exp(-0.5 * u^2)
    a <- p[["amplitude"]]
    cbind(baseline = 1, amplitude = e, center = a * e * u / p[["sd"]],
          sd = a * e * u^2 / p[["sd"]])
  },
  canonical = function(p, free) {
    if ("sd" %in% free) p[["sd"]] <- abs(p[["sd"]])
    p
  },
  derived = list(
    list(name = "fwhm", value = function(p) FWHM_FACTOR * p[["sd"]], linear = c(sd = FWHM_FACTOR))
  )
)

curve <- function(p, x) model$f(p, x)
simulate <- function(x, p, sd, reps) lapply(seq_len(reps), function(i) curve(p, x) + rnorm(length(x), sd = sd))
case <- function(case, description, x, y, truth, at = NULL, ...) {
  if (is.null(at)) at <- truth[["center"]] + c(-3, -1, 0, 1, 3) * abs(truth[["sd"]])
  a <- abs(truth[["amplitude"]])
  nl_fixture(model, case, description, x, y, at = at, truth = truth,
             scale = c(baseline = 0.1 * a, amplitude = 0.2 * a, center = 0.5 * abs(truth[["sd"]])),
             relative = "sd", ...)
}

# --- Cases ---------------------------------------------------------------------------

x21 <- seq(0, 20, by = 1)
base <- c(baseline = 5, amplitude = 50, center = 10, sd = 2.5)

case("clean", "21 points, 2 replicates, noise 1% of the amplitude.", x21,
     simulate(x21, base, 0.5, 2), base)

# Realistic noise: SD 5-20% of the amplitude, 1-3 replicates, different designs.
noisy <- list(
  list(p = c(baseline = 5, amplitude = 50, center = 10, sd = 2.5), sd = 0.05, reps = 3, x = x21),
  list(p = c(baseline = 5, amplitude = 50, center = 10, sd = 2.5), sd = 0.10, reps = 2, x = x21),
  list(p = c(baseline = 5, amplitude = 50, center = 10, sd = 2.5), sd = 0.20, reps = 3, x = x21),
  list(p = c(baseline = 0, amplitude = 1200, center = 520, sd = 15), sd = 0.08, reps = 1, x = seq(450, 600, by = 5)),
  list(p = c(baseline = 0.2, amplitude = -0.15, center = 3.2, sd = 0.4), sd = 0.10, reps = 2, x = seq(1, 6, by = 0.2)),
  list(p = c(baseline = 100, amplitude = 30, center = 0, sd = 1), sd = 0.05, reps = 3, x = seq(-4, 4, by = 0.5)),
  list(p = c(baseline = 5, amplitude = 50, center = 6, sd = 2.5), sd = 0.12, reps = 2, x = x21),
  list(p = c(baseline = 5, amplitude = 50, center = 10, sd = 2.5), sd = 0.10, reps = 1, x = seq(0, 20, by = 2)),
  list(p = c(baseline = 1e4, amplitude = 2.5e5, center = 37.5, sd = 0.8), sd = 0.07, reps = 2, x = seq(33, 42, by = 0.5)),
  list(p = c(baseline = -20, amplitude = 80, center = 50, sd = 12), sd = 0.15, reps = 3, x = seq(0, 100, by = 5))
)
for (i in seq_along(noisy)) {
  s <- noisy[[i]]
  case(sprintf("noisy_%02d", i),
       sprintf("Noise SD %.0f%% of the amplitude, %d replicate(s), %d points.", 100 * s$sd, s$reps, length(s$x)),
       s$x, simulate(s$x, s$p, s$sd * abs(s$p[["amplitude"]]), s$reps), s$p)
}

y_base <- simulate(x21, base, 5, 2)
case("x_range", "The 10% noise data fitted on 4 <= X <= 18 only.", x21, y_base, base, x_min = 4, x_max = 18)
y_m <- y_base
y_m[[1]][c(3, 11)] <- NaN
y_m[[2]][c(7, 8)] <- NaN
x_m <- x21
x_m[15] <- NaN
case("missing", "Missing X and Y values: unbalanced replicates per row.", x_m, y_m, base)
case("ties", "Responses rounded to multiples of 10: many tied values.", x21,
     lapply(y_base, function(v) round(v / 10) * 10), base)
p0 <- c(baseline = 0, amplitude = 50, center = 10, sd = 2.5)
case("fixed_baseline", "Baseline fixed at 0: the plain Gaussian, 3 free parameters.", x21,
     simulate(x21, p0, 2.5, 2), p0, fixed = c(baseline = 0))
case("n_small", "Five points, one replicate: df = 1.", c(4, 8, 10, 12, 16),
     list(curve(base, c(4, 8, 10, 12, 16)) + c(1, -2, 1.5, 0.5, -1)), base)
case("n_two", "Two points: fewer than the parameters, no fit.", c(5, 10), list(c(10, 50)),
     base, expect_fit = FALSE)
case("constant", "Every response is 42: Center and SD are not determined.", x21,
     list(rep(42, 21), rep(42, 21)), c(baseline = 40, amplitude = 5, center = 10, sd = 3),
     expect_fit = FALSE)
# A straight line is approached by a Gaussian whose center and width go to infinity.
case("no_converge", "An exact straight line: the fit has no minimum (Center, SD -> Inf).", x21,
     list(2 + 0.5 * x21), c(baseline = 0, amplitude = 15, center = 25, sd = 10), expect_fit = FALSE)

nl_summary(model)
