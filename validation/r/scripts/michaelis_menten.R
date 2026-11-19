# Michaelis-Menten enzyme kinetics, with each parameter's SE and 95% CI, R², Sy.x and
# the 95% confidence band.
#
# Curvis: curvis.analysis.fit.kinetics. Equation and definitions: docs/models.md.
#
#   Y = Vmax * X / (Km + X)
#
# X is the substrate concentration, Y the initial velocity. Vmax (maximum velocity, Y
# units) and Km (Michaelis constant, X units) are unbounded.
# Fit, SE, CI, band and the no-fit rules: validation/r/R/nonlinear.R.
# Cross-check: stats::nls with the self-starting SSmicmen (Vm = Vmax, K = Km) must give
# the same parameters to 1e-6 where it converges.
# The NIST StRD dataset Misra1d, y = b1 * b2 * x / (1 + b2 * x), is this model with
# Vmax = b1 and Km = 1 / b2; Curvis is also tested on it (tests/validation, nist_nls).

source(file.path(r_project_dir(), "R", "nonlinear.R"), local = TRUE)

model <- list(
  name = "michaelis_menten",
  params = c("vmax", "km"),
  log_x = FALSE,
  f = function(p, x) p[["vmax"]] * x / (p[["km"]] + x),
  grad = function(p, x) {
    d <- p[["km"]] + x
    cbind(vmax = x / d, km = -p[["vmax"]] * x / d^2)
  },
  canonical = function(p, free) p,
  derived = list()
)

ssmicmen <- function(x, y, fixed) {
  d <- data.frame(x = x, y = y)
  start <- getInitial(y ~ SSmicmen(x, Vm, K), data = d)
  cf <- tight_nls(y ~ SSmicmen(x, Vm, K), d, start)
  c(vmax = cf[["Vm"]], km = cf[["K"]])
}

curve <- function(p, x) model$f(p, x)
simulate <- function(x, p, sd, reps) lapply(seq_len(reps), function(i) curve(p, x) + rnorm(length(x), sd = sd))
case <- function(case, description, x, y, truth, at = c(0, 1, 5, 20, 50), ...) {
  nl_fixture(model, case, description, x, y, at = at, truth = truth,
             relative = c("vmax", "km"), crosscheck = ssmicmen, ...)
}

# --- Cases ---------------------------------------------------------------------------

s9 <- c(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64)                # µM
base <- c(vmax = 120, km = 6)

case("clean", "9 substrate concentrations, 2 replicates, noise 1% of Vmax.", s9,
     simulate(s9, base, 1.2, 2), base)

# Realistic noise: SD 5-20% of Vmax, 1-3 replicates, different designs.
noisy <- list(
  list(p = c(vmax = 120, km = 6), sd = 0.05, reps = 3, x = s9),
  list(p = c(vmax = 120, km = 6), sd = 0.10, reps = 2, x = s9),
  list(p = c(vmax = 120, km = 6), sd = 0.20, reps = 3, x = s9),
  list(p = c(vmax = 0.85, km = 0.04), sd = 0.08, reps = 1, x = c(0.005, 0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28)),
  list(p = c(vmax = 3500, km = 250), sd = 0.10, reps = 2, x = c(25, 50, 100, 200, 400, 800, 1600, 3200)),
  list(p = c(vmax = 120, km = 6), sd = 0.05, reps = 3, x = c(0, 1, 2, 3, 5, 7.5, 10, 15, 20, 30, 50)),
  list(p = c(vmax = 60, km = 30), sd = 0.12, reps = 2, x = c(2, 5, 10, 20, 40, 80, 160)),
  list(p = c(vmax = 120, km = 6), sd = 0.10, reps = 1, x = c(1, 2, 5, 10, 20, 50)),
  list(p = c(vmax = 1.5e-3, km = 2e-6), sd = 0.07, reps = 2, x = 1e-6 * c(0.2, 0.5, 1, 2, 5, 10, 20, 50)),
  list(p = c(vmax = 45, km = 2), sd = 0.15, reps = 3, x = c(0.5, 1, 2, 4, 8, 16))
)
for (i in seq_along(noisy)) {
  s <- noisy[[i]]
  case(sprintf("noisy_%02d", i),
       sprintf("Noise SD %.0f%% of Vmax, %d replicate(s), %d concentrations.", 100 * s$sd, s$reps, length(s$x)),
       s$x, simulate(s$x, s$p, s$sd * s$p[["vmax"]], s$reps), s$p,
       at = c(0, 0.5, 1, 2) * s$p[["km"]])
}

y_base <- simulate(s9, base, 12, 2)
case("x_range", "The 10% noise data fitted on 0.5 <= X <= 32 only.", s9, y_base, base,
     x_min = 0.5, x_max = 32)
y_m <- y_base
y_m[[1]][c(2, 7)] <- NaN
y_m[[2]][4] <- NaN
x_m <- s9
x_m[6] <- NaN
case("missing", "Missing X and Y values: unbalanced replicates per row.", x_m, y_m, base)
case("ties", "Velocities rounded to multiples of 10: many tied values.", s9,
     lapply(y_base, function(v) round(v / 10) * 10), base)
case("n_small", "Three concentrations, one replicate: df = 1.", c(1, 6, 50),
     list(curve(base, c(1, 6, 50)) + c(1.5, -2, 1)), base)
case("n_two", "Two points: an exact fit, df = 0, no SE.", c(2, 20), list(c(30, 90)), base)
case("n_one", "One point: fewer than the parameters, no fit.", 5, list(50), base,
     expect_fit = FALSE)
# Constant velocity: Km -> 0 makes the curve a step at X = 0 that matches every point
# exactly; the minimum lies on the limit Km = 0, where the curve is not defined at 0.
case("constant", "Every velocity is 42: the fit has no minimum (Km -> 0).", s9,
     list(rep(42, 9), rep(42, 9)), c(vmax = 42, km = 1), expect_fit = FALSE)
# A straight line through the origin is the limit Km -> Inf with Vmax / Km fixed.
case("no_converge", "An exact straight line through 0: no minimum (Km -> Inf).", s9,
     list(3 * s9), c(vmax = 300, km = 100), expect_fit = FALSE)

nl_summary(model)
