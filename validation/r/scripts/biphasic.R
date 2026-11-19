# Biphasic dose-response: two Hill components with their own LogEC50 and Hill slope,
# sharing Bottom and Top, with each parameter's SE and 95% CI, both EC50s with their
# CIs, R², Sy.x and the 95% confidence band.
#
# Curvis: curvis.analysis.fit.dose_response. Equation and definitions: docs/models.md.
#
# On X' = log10(concentration) (or X' = X when x_is_log):
#
#   Y = Bottom + (Top - Bottom) * [      Frac  / (1 + 10^((LogEC50_1 - X') * nH1))
#                                  + (1 - Frac) / (1 + 10^((LogEC50_2 - X') * nH2)) ]
#
# Frac is the fraction of the span in the first phase. Every parameter is unbounded.
# Reported form (the same curve, written one way), applied to free parameters only:
# 1. Top >= Bottom: if Top < Bottom, swap them and negate both Hill slopes
#    (needs Bottom, Top and both slopes free).
# 2. LogEC50_1 <= LogEC50_2: otherwise swap the two components (LogEC50, slope) and
#    replace Frac by 1 - Frac (needs Frac and both LogEC50s free, and the two slopes
#    both free or both fixed to the same value).
# EC50_i = 10^LogEC50_i with CI 10^(CI of LogEC50_i), as for the 4PL.
# Concentration 0 (X' = -Inf) takes the limit of the curve.
# Fit, SE, CI, band and the no-fit rules: validation/r/R/nonlinear.R.
# No other R implementation of this model to cross-check against (drc has none).

source(file.path(r_project_dir(), "R", "nonlinear.R"), local = TRUE)

model <- list(
  name = "biphasic",
  params = c("bottom", "top", "frac", "log_ec50_1", "log_ec50_2", "hill_slope_1", "hill_slope_2"),
  log_x = TRUE,
  f = function(p, x) {
    g1 <- logistic_g((p[["log_ec50_1"]] - x) * p[["hill_slope_1"]])
    g2 <- logistic_g((p[["log_ec50_2"]] - x) * p[["hill_slope_2"]])
    p[["bottom"]] + (p[["top"]] - p[["bottom"]]) * (p[["frac"]] * g1 + (1 - p[["frac"]]) * g2)
  },
  grad = function(p, x) {
    span <- p[["top"]] - p[["bottom"]]
    fr <- p[["frac"]]
    g1 <- logistic_g((p[["log_ec50_1"]] - x) * p[["hill_slope_1"]])
    g2 <- logistic_g((p[["log_ec50_2"]] - x) * p[["hill_slope_2"]])
    d1 <- logistic_dg(g1)
    d2 <- logistic_dg(g2)
    s <- fr * g1 + (1 - fr) * g2
    cbind(bottom = 1 - s, top = s, frac = span * (g1 - g2),
          log_ec50_1 = span * fr * d1 * p[["hill_slope_1"]],
          log_ec50_2 = span * (1 - fr) * d2 * p[["hill_slope_2"]],
          hill_slope_1 = span * fr * d1 * (p[["log_ec50_1"]] - x),
          hill_slope_2 = span * (1 - fr) * d2 * (p[["log_ec50_2"]] - x))
  },
  canonical = function(p, free) {
    if (all(c("bottom", "top", "hill_slope_1", "hill_slope_2") %in% free) && p[["top"]] < p[["bottom"]]) {
      p[c("bottom", "top")] <- p[c("top", "bottom")]
      p[c("hill_slope_1", "hill_slope_2")] <- -p[c("hill_slope_1", "hill_slope_2")]
    }
    slopes_free <- all(c("hill_slope_1", "hill_slope_2") %in% free)
    slopes_same <- !any(c("hill_slope_1", "hill_slope_2") %in% free) &&
      p[["hill_slope_1"]] == p[["hill_slope_2"]]
    if (all(c("frac", "log_ec50_1", "log_ec50_2") %in% free) && (slopes_free || slopes_same) &&
        p[["log_ec50_1"]] > p[["log_ec50_2"]]) {
      p[c("log_ec50_1", "log_ec50_2")] <- p[c("log_ec50_2", "log_ec50_1")]
      p[c("hill_slope_1", "hill_slope_2")] <- p[c("hill_slope_2", "hill_slope_1")]
      p[["frac"]] <- 1 - p[["frac"]]
    }
    p
  },
  derived = list(
    list(name = "ec50_1", from = "log_ec50_1", map = function(v) 10^v),
    list(name = "ec50_2", from = "log_ec50_2", map = function(v) 10^v)
  )
)

curve <- function(p, xl) model$f(p, xl)
simulate <- function(conc, p, sd, reps) {
  lapply(seq_len(reps), function(i) curve(p, log10(conc)) + rnorm(length(conc), sd = sd))
}
case <- function(case, description, x, y, truth, at = 10^seq(-10, -3, by = 1), ...) {
  span <- abs(truth[["top"]] - truth[["bottom"]])
  nl_fixture(model, case, description, x, y, at = at, truth = truth,
             scale = c(bottom = 0.05 * span, top = 0.05 * span, frac = 0.1,
                       log_ec50_1 = 0.3, log_ec50_2 = 0.3),
             relative = c("hill_slope_1", "hill_slope_2"), ...)
}

# --- Cases ---------------------------------------------------------------------------

conc <- 10^seq(-10, -3, by = 0.5)                       # molar, 15 concentrations
base <- c(bottom = 0, top = 100, frac = 0.4, log_ec50_1 = -8, log_ec50_2 = -5,
          hill_slope_1 = 1, hill_slope_2 = 1)

case("clean", "15 concentrations, 2 replicates, noise 1% of the span.", conc,
     simulate(conc, base, 1, 2), base)

# Realistic noise for a 7-parameter model: SD 3-8% of the span, 1-3 replicates.
p_b <- function(...) { p <- base; v <- c(...); p[names(v)] <- v; p }
noisy <- list(
  list(p = base, sd = 0.03, reps = 3, x = conc),
  list(p = base, sd = 0.05, reps = 2, x = conc),
  # The least-squares optimum of this draw is degenerate: the second phase becomes a
  # step (nH2 ~ 18) between two adjacent concentrations, whose position the data do not
  # determine (JᵀJ singular). Kept as realistic data on which a fit must fail cleanly.
  list(p = base, sd = 0.08, reps = 3, x = conc, expect = FALSE),
  list(p = p_b(frac = 0.7, log_ec50_1 = -8.5, log_ec50_2 = -5.5), sd = 0.05, reps = 2, x = conc),
  list(p = p_b(bottom = 100, top = 0, frac = 0.5, hill_slope_1 = 1.5, hill_slope_2 = 0.8,
               log_ec50_1 = -8.5, log_ec50_2 = -5.5),
       sd = 0.04, reps = 3, x = conc),
  list(p = p_b(bottom = 10, top = 250, frac = 0.3), sd = 0.05, reps = 3, x = 10^seq(-10, -3, by = 0.25)),
  list(p = p_b(frac = 0.5, log_ec50_1 = -7.5, log_ec50_2 = -5), sd = 0.04, reps = 3, x = conc),
  list(p = p_b(hill_slope_1 = 2, hill_slope_2 = 0.7), sd = 0.05, reps = 2, x = conc),
  list(p = p_b(bottom = 0.05, top = 1.2, frac = 0.6), sd = 0.06, reps = 3, x = conc),
  list(p = base, sd = 0.04, reps = 1, x = 10^seq(-10, -3, by = 0.25))
)
for (i in seq_along(noisy)) {
  s <- noisy[[i]]
  span <- abs(s$p[["top"]] - s$p[["bottom"]])
  case(sprintf("noisy_%02d", i),
       sprintf("Noise SD %.0f%% of the span, %d replicate(s), %d concentrations.", 100 * s$sd, s$reps, length(s$x)),
       s$x, simulate(s$x, s$p, s$sd * span, s$reps), s$p, expect_fit = !isFALSE(s$expect))
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
case("n_small", "Nine concentrations, one replicate: df = 2.", conc[c(1, 3, 4, 5, 7, 9, 10, 11, 14)],
     list(curve(base, log10(conc[c(1, 3, 4, 5, 7, 9, 10, 11, 14)])) + c(1, -1.5, 2, -1, 0.5, 1, -2, 1.5, -0.5)),
     base)
case("n_two", "Two points: fewer than the parameters, no fit.", conc[c(3, 12)], list(c(5, 95)),
     base, expect_fit = FALSE)
case("constant", "Every response is 42: nothing but the level is determined.", conc,
     list(rep(42, 15), rep(42, 15)), base, expect_fit = FALSE)
# Exactly monophasic (4PL) data: the two components merge, so Frac and the second
# component are not determined.
mono <- c(bottom = 0, top = 100, frac = 1, log_ec50_1 = -6.5, log_ec50_2 = -6.5,
          hill_slope_1 = 1, hill_slope_2 = 1)
case("no_converge", "Exact one-phase (4PL) data: the second phase is not determined.", conc,
     list(curve(mono, log10(conc))), base, expect_fit = FALSE)

nl_summary(model)
