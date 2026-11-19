# Bell-shaped dose-response: a rise from Plateau1 to the middle level Dip and a fall
# from Dip to Plateau2 (or a U for a Dip below the plateaus), with each parameter's SE
# and 95% CI, both EC50s, the derived peak center, width and amplitude with SE and CI,
# R², Sy.x and the 95% confidence band.
#
# Curvis: curvis.analysis.fit.dose_response. Equation and definitions: docs/models.md.
#
# On X' = log10(concentration) (or X' = X when x_is_log):
#
#   Y = Dip + (Plateau1 - Dip) / (1 + 10^((X' - LogEC50_1) * nH1))
#           + (Plateau2 - Dip) / (1 + 10^((LogEC50_2 - X') * nH2))
#
# With positive slopes and LogEC50_1 < LogEC50_2, Y is Plateau1 at low X, Dip between
# the two EC50s and Plateau2 at high X. Every parameter is unbounded; there is no
# second way of writing the same curve, so the parameters are reported as fitted.
# Derived values, linear in the parameters (SE = sqrt(cᵀVc), CI -/+ t * SE):
#   Center    = (LogEC50_1 + LogEC50_2) / 2      (log10 units, the middle of the peak)
#   Width     = LogEC50_2 - LogEC50_1            (log10 units)
#   Amplitude = Dip - (Plateau1 + Plateau2) / 2  (height of the peak above the plateaus)
# EC50_i = 10^LogEC50_i with CI 10^(CI of LogEC50_i).
# Concentration 0 (X' = -Inf) takes the limit of the curve.
# Fit, SE, CI, band and the no-fit rules: validation/r/R/nonlinear.R.
# No other R implementation of this model to cross-check against.

source(file.path(r_project_dir(), "R", "nonlinear.R"), local = TRUE)

model <- list(
  name = "bell_shaped",
  params = c("plateau_1", "dip", "plateau_2", "log_ec50_1", "log_ec50_2", "hill_slope_1",
             "hill_slope_2"),
  log_x = TRUE,
  f = function(p, x) {
    g1 <- logistic_g((x - p[["log_ec50_1"]]) * p[["hill_slope_1"]])
    g2 <- logistic_g((p[["log_ec50_2"]] - x) * p[["hill_slope_2"]])
    p[["dip"]] + (p[["plateau_1"]] - p[["dip"]]) * g1 + (p[["plateau_2"]] - p[["dip"]]) * g2
  },
  grad = function(p, x) {
    g1 <- logistic_g((x - p[["log_ec50_1"]]) * p[["hill_slope_1"]])
    g2 <- logistic_g((p[["log_ec50_2"]] - x) * p[["hill_slope_2"]])
    s1 <- p[["plateau_1"]] - p[["dip"]]
    s2 <- p[["plateau_2"]] - p[["dip"]]
    d1 <- logistic_dg(g1)
    d2 <- logistic_dg(g2)
    cbind(plateau_1 = g1, dip = 1 - g1 - g2, plateau_2 = g2,
          log_ec50_1 = -s1 * d1 * p[["hill_slope_1"]],
          log_ec50_2 = s2 * d2 * p[["hill_slope_2"]],
          hill_slope_1 = s1 * d1 * (x - p[["log_ec50_1"]]),
          hill_slope_2 = s2 * d2 * (p[["log_ec50_2"]] - x))
  },
  canonical = function(p, free) p,
  derived = list(
    list(name = "ec50_1", from = "log_ec50_1", map = function(v) 10^v),
    list(name = "ec50_2", from = "log_ec50_2", map = function(v) 10^v),
    list(name = "center", value = function(p) (p[["log_ec50_1"]] + p[["log_ec50_2"]]) / 2,
         linear = c(log_ec50_1 = 0.5, log_ec50_2 = 0.5)),
    list(name = "width", value = function(p) p[["log_ec50_2"]] - p[["log_ec50_1"]],
         linear = c(log_ec50_1 = -1, log_ec50_2 = 1)),
    list(name = "amplitude", value = function(p) p[["dip"]] - (p[["plateau_1"]] + p[["plateau_2"]]) / 2,
         linear = c(dip = 1, plateau_1 = -0.5, plateau_2 = -0.5))
  )
)

curve <- function(p, xl) model$f(p, xl)
simulate <- function(conc, p, sd, reps) {
  lapply(seq_len(reps), function(i) curve(p, log10(conc)) + rnorm(length(conc), sd = sd))
}
case <- function(case, description, x, y, truth, at = 10^seq(-10, -3, by = 1), ...) {
  a <- abs(truth[["dip"]] - (truth[["plateau_1"]] + truth[["plateau_2"]]) / 2)
  nl_fixture(model, case, description, x, y, at = at, truth = truth,
             scale = c(plateau_1 = 0.05 * a, dip = 0.05 * a, plateau_2 = 0.05 * a,
                       log_ec50_1 = 0.3, log_ec50_2 = 0.3),
             relative = c("hill_slope_1", "hill_slope_2"), ...)
}

# --- Cases ---------------------------------------------------------------------------

conc <- 10^seq(-10, -3, by = 0.5)                       # molar, 15 concentrations
base <- c(plateau_1 = 0, dip = 100, plateau_2 = 10, log_ec50_1 = -8, log_ec50_2 = -5,
          hill_slope_1 = 1, hill_slope_2 = 1)

case("clean", "15 concentrations, 2 replicates, noise 1% of the amplitude.", conc,
     simulate(conc, base, 1, 2), base)

# Realistic noise for a 7-parameter model: SD 3-8% of the amplitude, 1-3 replicates.
p_b <- function(...) { p <- base; v <- c(...); p[names(v)] <- v; p }
noisy <- list(
  list(p = base, sd = 0.03, reps = 3, x = conc),
  list(p = base, sd = 0.05, reps = 2, x = conc),
  list(p = base, sd = 0.08, reps = 3, x = conc),
  list(p = p_b(log_ec50_1 = -8.5, log_ec50_2 = -5.5, plateau_2 = 40), sd = 0.05, reps = 2, x = conc),
  list(p = p_b(plateau_1 = 100, dip = 20, plateau_2 = 90), sd = 0.04, reps = 3, x = conc),  # a U
  list(p = p_b(dip = 250, plateau_2 = 0), sd = 0.05, reps = 3, x = 10^seq(-10, -3, by = 0.25)),
  list(p = p_b(hill_slope_1 = 1.8, hill_slope_2 = 0.8), sd = 0.05, reps = 3, x = conc),
  list(p = p_b(log_ec50_1 = -7.5, log_ec50_2 = -5.5), sd = 0.04, reps = 3, x = conc),
  list(p = p_b(plateau_1 = 0.1, dip = 1.5, plateau_2 = 0.3), sd = 0.06, reps = 3, x = conc),
  list(p = base, sd = 0.04, reps = 1, x = 10^seq(-10, -3, by = 0.25))
)
for (i in seq_along(noisy)) {
  s <- noisy[[i]]
  a <- abs(s$p[["dip"]] - (s$p[["plateau_1"]] + s$p[["plateau_2"]]) / 2)
  case(sprintf("noisy_%02d", i),
       sprintf("Noise SD %.0f%% of the amplitude, %d replicate(s), %d concentrations.", 100 * s$sd, s$reps, length(s$x)),
       s$x, simulate(s$x, s$p, s$sd * a, s$reps), s$p, expect_fit = !isFALSE(s$expect))
}

y_base <- simulate(conc, base, 4, 3)
case("log_x", "4% noise data with X entered as log10(concentration).", log10(conc), y_base, base,
     at = seq(-10, -3, by = 1), x_is_log = TRUE)
conc0 <- c(0, conc)
case("zero_control", "A 0 control in front of the 15 concentrations, 2 replicates.", conc0,
     simulate(conc0, base, 3, 2), base, at = c(0, 1e-9, 1e-7, 1e-5, 1e-3))
case("x_range", "The 4% noise data fitted on 1e-9.5 <= X <= 1e-3.5 only.", conc, y_base, base,
     x_min = 10^-9.5, x_max = 10^-3.5)
y_m <- y_base
y_m[[1]][c(2, 9)] <- NaN
y_m[[2]][c(5, 12)] <- NaN
y_m[[3]][14] <- NaN
x_m <- conc
x_m[7] <- NaN
case("missing", "Missing X and Y values: unbalanced replicates per row.", x_m, y_m, base)
case("ties", "Responses rounded to multiples of 5: many tied values.", conc,
     lapply(y_base, function(v) round(v / 5) * 5), base)
case("fixed_slopes", "Both Hill slopes fixed at 1: 5 free parameters.", conc, y_base, base,
     fixed = c(hill_slope_1 = 1, hill_slope_2 = 1))
case("n_small", "Nine concentrations, one replicate: df = 2.", conc[c(1, 3, 5, 6, 7, 9, 10, 12, 15)],
     list(curve(base, log10(conc[c(1, 3, 5, 6, 7, 9, 10, 12, 15)])) + c(1, -1.5, 2, -1, 0.5, 1, -2, 1.5, -0.5)),
     base)
case("n_two", "Two points: fewer than the parameters, no fit.", conc[c(3, 12)], list(c(5, 95)),
     base, expect_fit = FALSE)
case("constant", "Every response is 42: nothing but the level is determined.", conc,
     list(rep(42, 15), rep(42, 15)), base, expect_fit = FALSE)
# Exactly monotonic (4PL) data: the fall back to Plateau2 never happens, so the second
# transition is not determined (LogEC50_2 -> Inf).
mono <- c(plateau_1 = 0, dip = 100, plateau_2 = 100, log_ec50_1 = -6.5, log_ec50_2 = 0,
          hill_slope_1 = 1, hill_slope_2 = 1)
case("no_converge", "Exact one-phase rising (4PL) data: the fall is not determined.", conc,
     list(curve(mono, log10(conc))), base, expect_fit = FALSE)

nl_summary(model)
