# One-phase exponential decay, with each parameter's SE and 95% CI, the span, the
# half-life and tau with their CIs, R², Sy.x and the 95% confidence band.
#
# Curvis: curvis.analysis.fit.kinetics. Equation and definitions: docs/models.md.
#
#   Y = Plateau + (Y0 - Plateau) * exp(-K * X)
#
# Parameters Y0 (Y at X = 0), Plateau (Y at X -> Inf, for K > 0) and K (rate constant,
# 1/X units; unbounded: K < 0 is a growing curve). Derived:
# - Span = Y0 - Plateau, linear in the parameters: SE = sqrt(cᵀVc), CI -/+ t * SE.
# - Half-life = ln(2) / K and tau = 1 / K, for K > 0 only (NaN otherwise); CI = the
#   same function of the CI of K; when the lower limit of K is <= 0 the upper limit is Inf.
# Fit, SE, CI, band and the no-fit rules: validation/r/R/nonlinear.R.
# Cross-check: stats::nls with the self-starting SSasymp (Asym = Plateau, R0 = Y0,
# K = exp(lrc)) must give the same parameters to 1e-6 where it converges.

source(file.path(r_project_dir(), "R", "nonlinear.R"), local = TRUE)

model <- list(
  name = "exp_decay",
  params = c("y0", "plateau", "k"),
  log_x = FALSE,
  f = function(p, x) p[["plateau"]] + (p[["y0"]] - p[["plateau"]]) * exp(-p[["k"]] * x),
  grad = function(p, x) {
    e <- exp(-p[["k"]] * x)
    cbind(y0 = e, plateau = 1 - e, k = -(p[["y0"]] - p[["plateau"]]) * x * e)
  },
  canonical = function(p, free) p,
  derived = list(
    list(name = "span", value = function(p) p[["y0"]] - p[["plateau"]],
         linear = c(y0 = 1, plateau = -1)),
    list(name = "half_life", from = "k", map = function(k) log(2) / k, reciprocal = TRUE),
    list(name = "tau", from = "k", map = function(k) 1 / k, reciprocal = TRUE)
  )
)

ssasymp <- function(x, y, fixed) {
  if (length(fixed)) return(NULL)
  d <- data.frame(x = x, y = y)
  # The self-start's own initial fit with default settings, then nls with a tight
  # tolerance (passing it to the first call also tightens the self-start's fit).
  start <- getInitial(y ~ SSasymp(x, Asym, R0, lrc), data = d)
  cf <- tight_nls(y ~ SSasymp(x, Asym, R0, lrc), d, start)
  c(y0 = cf[["R0"]], plateau = cf[["Asym"]], k = exp(cf[["lrc"]]))
}

curve <- function(p, x) model$f(p, x)
simulate <- function(x, p, sd, reps) lapply(seq_len(reps), function(i) curve(p, x) + rnorm(length(x), sd = sd))
case <- function(case, description, x, y, truth, ...) {
  span <- abs(truth[["y0"]] - truth[["plateau"]])
  nl_fixture(model, case, description, x, y, at = c(0, 2, 5, 10, 20), truth = truth,
             scale = c(y0 = 0.2 * span, plateau = 0.2 * span), relative = "k",
             crosscheck = ssasymp, ...)
}

# --- Cases ---------------------------------------------------------------------------

t11 <- c(0, 1, 2, 3, 4, 6, 8, 10, 15, 20, 30)            # minutes
base <- c(y0 = 100, plateau = 10, k = 0.15)

case("clean", "11 times, 2 replicates, noise 1% of the span.", t11, simulate(t11, base, 0.9, 2), base)

# Realistic noise: SD 5-20% of the span, 1-3 replicates, different designs.
noisy <- list(
  list(p = c(y0 = 100, plateau = 10, k = 0.15), sd = 0.05, reps = 3, x = t11),
  list(p = c(y0 = 100, plateau = 10, k = 0.15), sd = 0.10, reps = 2, x = t11),
  list(p = c(y0 = 100, plateau = 10, k = 0.15), sd = 0.20, reps = 3, x = t11),
  list(p = c(y0 = 2000, plateau = 150, k = 0.5), sd = 0.08, reps = 1, x = seq(0, 12, by = 0.5)),
  list(p = c(y0 = 1.2, plateau = 0.3, k = 0.03), sd = 0.10, reps = 2, x = seq(0, 120, by = 10)),
  list(p = c(y0 = 50, plateau = 0, k = 1.2), sd = 0.05, reps = 3, x = c(0, 0.25, 0.5, 1, 1.5, 2, 3, 4, 6)),
  list(p = c(y0 = 80, plateau = 40, k = 0.2), sd = 0.15, reps = 2, x = t11),
  list(p = c(y0 = 100, plateau = 10, k = 0.15), sd = 0.10, reps = 1, x = c(0, 2, 5, 10, 20, 40)),
  list(p = c(y0 = 5e4, plateau = 2e3, k = 0.0012), sd = 0.07, reps = 2, x = seq(0, 3600, by = 300)),
  list(p = c(y0 = -10, plateau = -60, k = 0.3), sd = 0.12, reps = 3, x = t11)
)
for (i in seq_along(noisy)) {
  s <- noisy[[i]]
  span <- abs(s$p[["y0"]] - s$p[["plateau"]])
  case(sprintf("noisy_%02d", i),
       sprintf("Noise SD %.0f%% of the span, %d replicate(s), %d times.", 100 * s$sd, s$reps, length(s$x)),
       s$x, simulate(s$x, s$p, s$sd * span, s$reps), s$p)
}

y_base <- simulate(t11, base, 5, 2)
case("x_range", "The 10% noise data fitted on 1 <= X <= 20 only.", t11, y_base, base, x_min = 1, x_max = 20)
y_m <- y_base
y_m[[1]][c(2, 7)] <- NaN
y_m[[2]][5] <- NaN
x_m <- t11
x_m[9] <- NaN
case("missing", "Missing X and Y values: unbalanced replicates per row.", x_m, y_m, base)
case("ties", "Responses rounded to multiples of 10: many tied values.", t11,
     lapply(y_base, function(v) round(v / 10) * 10), base)
p0 <- c(y0 = 100, plateau = 0, k = 0.15)
case("fixed_plateau", "Plateau fixed at 0: decay to zero, 2 free parameters.", t11,
     simulate(t11, p0, 4, 2), p0, fixed = c(plateau = 0))
case("n_small", "Four times, one replicate: df = 1.", c(0, 3, 10, 30),
     list(curve(base, c(0, 3, 10, 30)) + c(2, -3, 1, -1)), base)
case("n_two", "Two points: fewer than the parameters, no fit.", c(0, 10), list(c(100, 30)),
     base, expect_fit = FALSE)
case("constant", "Every response is 42: K is not determined.", t11, list(rep(42, 11), rep(42, 11)),
     c(y0 = 42, plateau = 40, k = 0.1), expect_fit = FALSE)
# A straight line is the limit K -> 0 with Y0 - Plateau -> Inf: no minimum.
case("no_converge", "An exact straight line: the fit has no minimum (K -> 0).", t11,
     list(100 - 2 * t11), c(y0 = 100, plateau = 0, k = 0.05), expect_fit = FALSE)

nl_summary(model)
