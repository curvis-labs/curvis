# Linear regression (degree 1) and polynomials of degree 2-6, fitted by least squares,
# with each coefficient's SE and 95% CI, R², Sy.x and the 95% confidence band.
#
# Curvis: curvis.analysis.fit.linear.
#
# Definitions (stats::lm, predict(interval = "confidence"), 95% confidence):
# - Data: one X column and one or more replicate Y columns (each the length of X). Every
#   replicate value is its own point, paired with the X of its row. A point with a
#   missing X or Y (NaN) is left out, and so is a point outside the X range
#   x_min <= X <= x_max (the limits are -Inf/Inf when the fit is not limited).
# - Model: Y = b0 + b1 X + ... + bd X^d (raw powers, lm(y ~ poly(x, d, raw = TRUE))).
#   Coefficients are written in that order: intercept, slope (X), X², ...
# - n: points used; df = n - (d + 1).
# - SE: from summary(lm); CI: estimate -/+ qt(0.975, df) * SE (confint).
# - R² = 1 - RSS / TSS with TSS = sum((Y - mean(Y))²) (equal to summary.lm's r.squared
#   for a model with an intercept). Sy.x = sqrt(RSS / df).
# - Band: predict(interval = "confidence") at the X values in `at`: the fitted value and
#   fitted -/+ qt(0.975, df) * SE of the fitted value.
#
# Cases lm cannot answer, and the stated expected values:
# - Too few points (n < d + 1) or too few distinct X values for the degree (lm gives an
#   NA coefficient): no fit. fit_ok = false and every other output except n is NaN.
# - df = 0 (exactly d + 1 points): the curve goes through every point. Coefficients,
#   R² = 1 and the fitted band values are reported; SE, Sy.x, CI and band limits are NaN.
# - Constant Y (every point has the same Y): the fit is exact: b0 = Y, every other
#   coefficient is 0, SE = 0, Sy.x = 0, the CI and band collapse onto the estimate, and
#   R² is NaN (0 / 0). lm only reaches these values up to rounding (its SEs are then
#   rounding noise), so they are written here and checked against lm.

fit_points <- function(x, y, x_min, x_max) {
  xs <- rep(as.double(x), times = length(y))
  ys <- unlist(lapply(y, as.double))
  keep <- !is.na(xs) & !is.na(ys) & xs >= x_min & xs <= x_max
  list(x = xs[keep], y = ys[keep])
}

# expect_fit: whether the case is meant to give a fit (checked, so a case cannot quietly
# change meaning, e.g. when lm's rank check drops a column).
polynomial_fixture <- function(case, description, x, y, degree, at,
                               x_min = -Inf, x_max = Inf, expect_fit = TRUE) {
  stopifnot(degree >= 1L, degree <= 6L, is.list(y), all(lengths(y) == length(x)))
  p <- fit_points(x, y, x_min, x_max)
  n <- length(p$x)
  k <- degree + 1L
  nan_k <- rep(NaN, k)
  nan_at <- rep(NaN, length(at))
  out <- list(
    fit_ok = FALSE, n = as.integer(n), df = NaN,
    coefficients = nan_k, se = nan_k, ci_lower = nan_k, ci_upper = nan_k,
    r_squared = NaN, sy_x = NaN,
    band_fit = nan_at, band_lower = nan_at, band_upper = nan_at
  )
  model <- NULL
  if (n >= k) {
    d <- data.frame(x = p$x, y = p$y)
    model <- lm(y ~ poly(x, degree, raw = TRUE), data = d)
    if (anyNA(coef(model))) model <- NULL  # singular design: too few distinct X values
  }
  if (!is.null(model)) {
    out$fit_ok <- TRUE
    df <- n - k
    out$df <- df
    newdata <- data.frame(x = as.double(at))
    if (length(unique(p$y)) == 1L) {
      # Constant Y: the exact fit, written by rule (see above) and checked against lm.
      b <- c(p$y[1], rep(0, degree))
      stopifnot(isTRUE(all.equal(unname(coef(model)), b, tolerance = 1e-10,
                                 scale = max(1, abs(p$y[1])))))
      out$coefficients <- b
      out$band_fit <- rep(p$y[1], length(at))
      if (df > 0) {
        out$se <- rep(0, k)
        out$ci_lower <- b
        out$ci_upper <- b
        out$sy_x <- 0
        out$band_lower <- out$band_fit
        out$band_upper <- out$band_fit
      }
    } else {
      out$coefficients <- unname(coef(model))
      out$band_fit <- unname(predict(model, newdata = newdata))
      rss <- sum(residuals(model)^2)
      tss <- sum((p$y - mean(p$y))^2)
      out$r_squared <- if (df > 0) 1 - rss / tss else 1
      if (df > 0) {
        s <- summary(model)
        stopifnot(isTRUE(all.equal(out$r_squared, s$r.squared, tolerance = 1e-12)))
        out$se <- unname(s$coefficients[, "Std. Error"])
        ci <- confint(model, level = 0.95)
        out$ci_lower <- unname(ci[, 1])
        out$ci_upper <- unname(ci[, 2])
        out$sy_x <- s$sigma
        band <- predict(model, newdata = newdata, interval = "confidence", level = 0.95)
        out$band_lower <- unname(band[, "lwr"])
        out$band_upper <- unname(band[, "upr"])
      }
    }
  }
  if (out$fit_ok != expect_fit) stop(case, ": fit_ok is ", out$fit_ok)
  kinds <- c(
    fit_ok = "exact", n = "exact", df = "exact",
    coefficients = "parameter", se = "parameter", ci_lower = "ci", ci_upper = "ci",
    r_squared = "statistic", sy_x = "statistic",
    band_fit = "parameter", band_lower = "ci", band_upper = "ci"
  )
  write_fixture(
    analysis = "linear_fit",
    case = case,
    description = description,
    inputs = list(x = as.double(x), y = lapply(y, as.double), degree = scalar(as.integer(degree)),
                  x_min = scalar(as.double(x_min)), x_max = scalar(as.double(x_max)),
                  at = as.double(at)),
    expected = out,
    kinds = kinds,
    packages = "stats"
  )
}

# --- Linear regression ---------------------------------------------------------------

x10 <- as.double(1:10)
y10 <- 2 + 0.5 * x10 + rnorm(10, sd = 0.4)
at10 <- seq(0, 11, length.out = 12)

polynomial_fixture("line_basic", "Ten points around a straight line.",
                   x10, list(y10), 1L, at10)

x6 <- c(0, 2, 4, 6, 8, 10)
polynomial_fixture("line_replicates", "Six X values with three replicate Y columns.",
                   x6, lapply(1:3, function(i) 1.5 - 0.8 * x6 + rnorm(6, sd = 0.6)), 1L,
                   seq(0, 10, by = 2.5))

polynomial_fixture("line_missing", "Missing X and Y values, unequal replicates per row.",
                   c(1, 2, NaN, 4, 5, 6, 7, 8),
                   list(c(3.1, 4.9, 6.0, NaN, 10.8, 13.2, 14.9, 17.1),
                        c(2.8, NaN, 7.1, 9.2, 11.1, NaN, 15.3, 16.8)),
                   1L, c(1, 4.5, 8))

polynomial_fixture("line_n_two", "Two points: the line goes through both (df = 0).",
                   c(1, 3), list(c(2.5, 6.1)), 1L, c(0, 2, 4))

polynomial_fixture("line_n_three", "Three points: df = 1.",
                   c(1, 2, 4), list(c(2.5, 3.9, 8.2)), 1L, c(1, 2.5, 4))

polynomial_fixture("line_constant_y", "Every Y is 4.2: the line is flat and exact.",
                   x10, list(rep(4.2, 10)), 1L, c(0, 5, 10))

polynomial_fixture("line_constant_x", "Every X is 3: no line can be fitted.",
                   rep(3, 5), list(c(1.2, 2.3, 1.9, 2.8, 2.0)), 1L, c(3),
                   expect_fit = FALSE)

polynomial_fixture("line_ties", "Repeated X and Y values.",
                   c(1, 1, 2, 2, 3, 3, 4, 4), list(c(2, 3, 3, 3, 5, 4, 6, 6)), 1L, c(1, 2.5, 4))

# Y stays small: with Y near 1e9, lm's fitted values round at 1e-7 and its residual SD
# is then only good to ~1e-7, too coarse for a reference.
polynomial_fixture("line_large_offset", "X near 1e6 with small spread.",
                   1e6 + x10, list(20 + 0.25 * x10 + rnorm(10, sd = 0.3)), 1L,
                   1e6 + c(1, 5.5, 10))

polynomial_fixture("line_x_range", "The line_basic data fitted on 3 <= X <= 8 only.",
                   x10, list(y10), 1L, at10, x_min = 3, x_max = 8)

polynomial_fixture("line_negative_slope", "Decreasing data with large scatter.",
                   x10, list(50 - 3.1 * x10 + rnorm(10, sd = 5)), 1L, at10)

# --- Polynomials ---------------------------------------------------------------------

xp <- seq(0, 10, by = 0.5)
yp <- 3 + 1.2 * xp - 0.9 * xp^2 + 0.07 * xp^3 + rnorm(length(xp), sd = 0.8)
atp <- seq(0, 10, by = 1)
for (degree in 2:6) {
  polynomial_fixture(paste0("poly", degree, "_basic"),
                     paste0("21 points around a cubic, fitted with degree ", degree, "."),
                     xp, list(yp), degree, atp)
}

polynomial_fixture("poly2_replicates", "Quadratic with two replicate columns.",
                   x6, lapply(1:2, function(i) 10 + 2 * x6 - 0.3 * x6^2 + rnorm(6, sd = 0.5)),
                   2L, seq(0, 10, by = 2.5))

polynomial_fixture("poly2_missing", "Quadratic with missing values.",
                   c(0, 1, 2, 3, NaN, 5, 6, 7),
                   list(c(1.1, NaN, 5.2, 10.1, 16.8, 26.3, NaN, 50.2)), 2L, c(0, 3.5, 7))

polynomial_fixture("poly2_exact", "Three points for degree 2: the curve goes through them.",
                   c(0, 1, 3), list(c(1, 2.5, 4)), 2L, c(0, 2, 3))

polynomial_fixture("poly3_too_few", "Three points cannot determine a cubic.",
                   c(0, 1, 3), list(c(1, 2.5, 4)), 3L, c(0, 2, 3),
                   expect_fit = FALSE)

polynomial_fixture("poly4_too_few_x", "Ten points but only three distinct X values for degree 4.",
                   rep(c(1, 2, 3), length.out = 10),
                   list(c(1.0, 4.1, 9.2, 1.1, 3.9, 8.8, 0.9, 4.0, 9.1, 1.2)), 4L, c(1, 2, 3),
                   expect_fit = FALSE)

polynomial_fixture("poly2_constant_y", "Every Y is -1.5: the curve is flat and exact.",
                   xp, list(rep(-1.5, length(xp))), 2L, c(0, 5, 10))

polynomial_fixture("poly3_x_range", "The cubic data fitted on 2 <= X <= 9 only.",
                   xp, list(yp), 3L, atp, x_min = 2, x_max = 9)

polynomial_fixture("poly6_wide_x", "Degree 6 on X from 10 to 100: large powers of X.",
                   seq(10, 100, by = 5),
                   list(5 + 0.02 * seq(10, 100, by = 5)^2 + rnorm(19, sd = 3)), 6L,
                   c(10, 40, 70, 100))
