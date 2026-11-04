# Dose-response fits: the four-parameter logistic (Hill 4PL, Hill slope of either sign)
# and the three-parameter logistic with the bottom fixed at 0 (3PL), with each
# parameter's SE and 95% CI, EC50 with its 95% CI, R², Sy.x and the 95% confidence band.
#
# Curvis: curvis.analysis.fit.dose_response.
#
# Model, on X' = log10(concentration):
#
#   Y = Bottom + (Top - Bottom) / (1 + 10^((logEC50 - X') * HillSlope))
#
# A positive Hill slope rises with the concentration, a negative one falls. For 4PL the
# parameters are reported with Top >= Bottom (the same curve can also be written with
# Top and Bottom swapped and the slope negated; see canonical()). 3PL is the
# same model with Bottom = 0. This is drc's LL.4 / LL.3 with b = -HillSlope,
# c = Bottom, d = Top, e = EC50.
#
# Definitions:
# - Data: one X column and one or more replicate Y columns. Every replicate value is its
#   own point, paired with the X of its row; a point with a missing X or Y is left out.
# - X holds concentrations (x_is_log = false; X' = log10(X), and X = 0 is allowed: it
#   is the limit of the curve, Bottom for a positive slope, Top for a negative one; a
#   negative concentration is left out) or log10 concentrations (x_is_log = true; X' = X).
# - X range: points with x_min <= X <= x_max are used, in the units of the X column.
# - Fit: unweighted least squares. drc::drm finds the fit; minpack.lm::nlsLM, started
#   from it with tight tolerances, refines it (drc stops at a relative change of 1e-7,
#   too coarse for a 1e-6 comparison of parameters), Gauss-Newton steps with the
#   analytic Jacobian finish it (polish()), and the script checks that the result is a
#   minimum and that drc found the same one.
# - SE: asymptotic, sqrt(diag(s² (JᵀJ)⁻¹)) with the analytic Jacobian J at the optimum
#   and s² = RSS / df, df = n - number of fitted parameters (checked against nlsLM's
#   vcov; drc's vcov uses the Hessian of the RSS instead, see fit_logistic()).
# - CI of a parameter: estimate -/+ qt(0.975, df) * SE.
# - EC50 = 10^logEC50 and its CI is 10^(CI of logEC50) (asymmetric).
# - R² = 1 - RSS / TSS; Sy.x = sqrt(RSS / df).
# - Band at the X values in `at` (same units as X): fitted value -/+ qt(0.975, df) * SE
#   of the fitted value, SE = sqrt(g V gᵀ) with g the gradient of the model in the
#   parameters (the delta method, as predict.drc(interval = "confidence")).
#
# Cases without a fit, and the stated expected values (fit_ok = false; every output
# except n is NaN):
# - Fewer points than fitted parameters.
# - The fit does not converge, or converges where the parameters are not determined:
#   drm or nlsLM fails, nlsLM reports no convergence, JᵀJ is numerically singular
#   (reciprocal condition number < 1e-12), or two parameters are correlated with
#   |r| > 0.9999 (Curvis calls such a fit ambiguous and reports no parameters). Cases
#   meant to fail are marked expect_fit = FALSE and the script checks that they fail.
# A fit with df = 0 has no SE, CI, Sy.x or band limits (NaN).

LN10 <- log(10)

# Relative differences between Curvis' Hill slope SE and drc's, printed at the end.
.drc_se <- new.env()
.drc_se$diff <- double()

# The model and its gradient in (bottom, top, log_ec50, hill_slope). At X' = -Inf
# (concentration 0) they take their limits.
ll4 <- function(p, xl) {
  b <- p[["bottom"]]; t <- p[["top"]]; l <- p[["log_ec50"]]; h <- p[["hill_slope"]]
  u <- 10^((l - xl) * h)
  y <- b + (t - b) / (1 + u)
  y[is.infinite(xl)] <- if (h > 0) b else if (h < 0) t else (b + t) / 2
  y
}

ll4_gradient <- function(p, xl) {
  b <- p[["bottom"]]; t <- p[["top"]]; l <- p[["log_ec50"]]; h <- p[["hill_slope"]]
  u <- 10^((l - xl) * h)
  g <- 1 / (1 + u)
  w <- u * LN10 / (1 + u)^2
  out <- cbind(bottom = 1 - g, top = g,
               log_ec50 = -(t - b) * w * h, hill_slope = -(t - b) * w * (l - xl))
  lim <- is.infinite(xl)
  if (any(lim)) {
    out[lim, ] <- matrix(c(as.double(h > 0), as.double(h < 0), 0, 0), nrow = sum(lim),
                         ncol = 4, byrow = TRUE)
    if (h == 0) out[lim, 1:2] <- 0.5
  }
  out
}

# Fits; returns NULL when there is no fit (see above), else a list of results.
fit_logistic <- function(xl, y, model) {
  free <- if (model == "4pl") c("bottom", "top", "log_ec50", "hill_slope")
          else c("top", "log_ec50", "hill_slope")
  n <- length(y)
  if (n < length(free)) return(NULL)
  conc <- 10^xl
  d <- data.frame(conc = conc, xl = xl, y = y)
  fct <- if (model == "4pl") drc::LL.4() else drc::LL.3()
  drm_fit <- tryCatch(drc::drm(y ~ conc, data = d, fct = fct), error = function(e) NULL)
  if (is.null(drm_fit)) return(NULL)
  cf <- coef(drm_fit)
  start <- list(top = cf[["d:(Intercept)"]], log_ec50 = log10(cf[["e:(Intercept)"]]),
                hill_slope = -cf[["b:(Intercept)"]])
  if (model == "4pl") start <- c(list(bottom = cf[["c:(Intercept)"]]), start)
  formula <- if (model == "4pl") y ~ model_value(xl, bottom, top, log_ec50, hill_slope)
             else y ~ model_value(xl, 0, top, log_ec50, hill_slope)
  nls_fit <- tryCatch(
    minpack.lm::nlsLM(formula, data = d, start = start,
                      control = minpack.lm::nls.lm.control(ftol = 1e-15, ptol = 1e-15,
                                                           maxiter = 1000)),
    error = function(e) NULL)
  if (is.null(nls_fit) || !isTRUE(nls_fit$convInfo$isConv)) return(NULL)
  est <- coef(nls_fit)
  p <- c(bottom = if (model == "4pl") est[["bottom"]] else 0, top = est[["top"]],
         log_ec50 = est[["log_ec50"]], hill_slope = est[["hill_slope"]])
  p <- canonical(polish(p, free, xl, y), model)
  r <- y - ll4(p, xl)
  rss <- sum(r^2)
  jac <- ll4_gradient(p, xl)[, free, drop = FALSE]
  jtj <- crossprod(jac)
  inv <- tryCatch(solve(jtj), error = function(e) NULL)
  if (is.null(inv) || rcond(jtj) < 1e-12) return(NULL)
  corr <- cov2cor(inv)
  if (max(abs(corr[upper.tri(corr)])) > 0.9999) return(NULL)
  df <- n - length(free)
  # The two fits found the same minimum, and nlsLM's is a minimum: the residuals are
  # orthogonal to every column of the Jacobian (cosine < 1e-7).
  drm_p <- canonical(c(bottom = if (model == "4pl") cf[["c:(Intercept)"]] else 0,
                       top = start$top, log_ec50 = start$log_ec50,
                       hill_slope = start$hill_slope), model)
  # drc stops at a relative change in RSS of 1e-7, which leaves poorly determined
  # parameters ~1e-4 from the minimum, so this only checks it is the same minimum.
  stopifnot(isTRUE(all.equal(p, drm_p, tolerance = 1e-3)),
            sum((y - ll4(drm_p, xl))^2) >= rss * (1 - 1e-12))
  if (rss > 0) {
    cosine <- abs(crossprod(jac, r)) / (sqrt(colSums(jac^2)) * sqrt(rss))
    if (max(cosine) > 1e-7) stop("nlsLM stopped short of the minimum (cosine ", max(cosine), ")")
  }
  # SEs agree with nlsLM's (s² (JᵀJ)⁻¹ too, with a numerical Jacobian, at its own end
  # point). drc's vcov() is not comparable: it inverts the numerical Hessian of the RSS,
  # which also holds the residuals' second-order term, so its SEs differ by a few % for
  # noisy data. That difference is only printed.
  if (df > 0) {
    se <- sqrt(diag(rss / df * inv))
    names(se) <- free
    se_nls <- sqrt(diag(vcov(nls_fit)))
    # canonical() may have swapped Top and Bottom (their SEs swap with them).
    if (model == "4pl" && est[["top"]] < est[["bottom"]]) {
      se_nls[c("bottom", "top")] <- se_nls[c("top", "bottom")]
    }
    stopifnot(isTRUE(all.equal(se[free], se_nls[free], tolerance = 1e-3)))
    se_drc <- sqrt(diag(vcov(drm_fit)))
    names(se_drc) <- names(cf)  # drc's vcov() has no dimnames; same order as coef()
    drc_diff <- abs(se[["hill_slope"]] / se_drc[["b:(Intercept)"]] - 1)
    .drc_se$diff <- c(.drc_se$diff, drc_diff)
  }
  list(p = p, free = free, rss = rss, df = df, jtj = jtj, n = n)
}

# nlsLM works with numerical derivatives and stops a little short of the minimum
# (cosine ~1e-6 below). Gauss-Newton steps with the analytic Jacobian, each halved until
# it lowers the RSS, finish the job; they stop when a step no longer lowers it.
polish <- function(p, free, xl, y) {
  rss <- sum((y - ll4(p, xl))^2)
  for (i in 1:100) {
    jac <- ll4_gradient(p, xl)[, free, drop = FALSE]
    step <- tryCatch(qr.solve(jac, y - ll4(p, xl)), error = function(e) NULL)
    if (is.null(step)) break
    improved <- FALSE
    for (k in 0:30) {
      q <- p
      q[free] <- p[free] + step / 2^k
      rss_q <- sum((y - ll4(q, xl))^2)
      if (rss_q < rss) {
        improved <- TRUE
        break
      }
    }
    if (!improved) break
    p <- q
    rss <- rss_q
  }
  p
}

# For 4PL, (Bottom, Top, logEC50, h) and (Top, Bottom, logEC50, -h) are the same curve.
# Curvis reports the form with Top >= Bottom (the sign of h says whether the curve rises
# or falls). 3PL has no such twin: Bottom is 0.
canonical <- function(p, model) {
  if (model == "4pl" && p[["top"]] < p[["bottom"]]) {
    p <- c(bottom = p[["top"]], top = p[["bottom"]], log_ec50 = p[["log_ec50"]],
           hill_slope = -p[["hill_slope"]])
  }
  p
}

# The model as nlsLM's formula calls it (it handles concentration 0, xl = -Inf).
model_value <- function(xl, bottom, top, log_ec50, hill_slope) {
  ll4(c(bottom = bottom, top = top, log_ec50 = log_ec50, hill_slope = hill_slope), xl)
}

logistic_fixture <- function(case, description, x, y, model, at, x_is_log = FALSE,
                             x_min = -Inf, x_max = Inf, expect_fit = TRUE) {
  stopifnot(model %in% c("4pl", "3pl"), is.list(y), all(lengths(y) == length(x)))
  xs <- rep(as.double(x), times = length(y))
  ys <- unlist(lapply(y, as.double))
  keep <- !is.na(xs) & !is.na(ys) & xs >= x_min & xs <= x_max
  if (!x_is_log) keep <- keep & xs >= 0
  xl <- if (x_is_log) xs[keep] else log10(xs[keep])
  ys <- ys[keep]
  at_l <- if (x_is_log) as.double(at) else log10(as.double(at))

  names_p <- if (model == "4pl") c("bottom", "top", "log_ec50", "hill_slope")
             else c("top", "log_ec50", "hill_slope")
  out <- list(fit_ok = FALSE, n = length(ys), df = NaN)
  for (nm in names_p) {
    out[[nm]] <- NaN
    out[[paste0(nm, "_se")]] <- NaN
    out[[paste0(nm, "_ci_lower")]] <- NaN
    out[[paste0(nm, "_ci_upper")]] <- NaN
  }
  out <- c(out, list(ec50 = NaN, ec50_ci_lower = NaN, ec50_ci_upper = NaN,
                     r_squared = NaN, sy_x = NaN,
                     band_fit = rep(NaN, length(at)), band_lower = rep(NaN, length(at)),
                     band_upper = rep(NaN, length(at))))

  f <- fit_logistic(xl, ys, model)
  if (!is.null(f)) {
    out$fit_ok <- TRUE
    out$df <- f$df
    fitted_at <- ll4(f$p, at_l)
    out$band_fit <- fitted_at
    out$r_squared <- 1 - f$rss / sum((ys - mean(ys))^2)
    for (nm in names_p) out[[nm]] <- f$p[[nm]]
    out$ec50 <- 10^f$p[["log_ec50"]]
    if (f$df > 0) {
      s2 <- f$rss / f$df
      v <- s2 * solve(f$jtj)
      se <- sqrt(diag(v))
      q <- qt(0.975, f$df)
      for (nm in names_p) {
        out[[paste0(nm, "_se")]] <- se[[nm]]
        out[[paste0(nm, "_ci_lower")]] <- f$p[[nm]] - q * se[[nm]]
        out[[paste0(nm, "_ci_upper")]] <- f$p[[nm]] + q * se[[nm]]
      }
      out$ec50_ci_lower <- 10^out$log_ec50_ci_lower
      out$ec50_ci_upper <- 10^out$log_ec50_ci_upper
      out$sy_x <- sqrt(s2)
      g <- ll4_gradient(f$p, at_l)[, f$free, drop = FALSE]
      se_fit <- sqrt(rowSums((g %*% v) * g))
      out$band_lower <- fitted_at - q * se_fit
      out$band_upper <- fitted_at + q * se_fit
    }
  }
  if (out$fit_ok != expect_fit) stop(case, ": fit_ok is ", out$fit_ok)

  kinds <- c(fit_ok = "exact", n = "exact", df = "exact")
  for (nm in names_p) {
    kinds[[nm]] <- "parameter"
    kinds[[paste0(nm, "_se")]] <- "parameter"
    kinds[[paste0(nm, "_ci_lower")]] <- "ci"
    kinds[[paste0(nm, "_ci_upper")]] <- "ci"
  }
  kinds <- c(kinds, ec50 = "parameter", ec50_ci_lower = "ci", ec50_ci_upper = "ci",
             r_squared = "statistic", sy_x = "parameter",
             band_fit = "parameter", band_lower = "ci", band_upper = "ci")
  out$n <- as.integer(out$n)
  write_fixture(
    analysis = "dose_response",
    case = paste0(case, "_", model),
    description = paste0(description, " (", toupper(model), ")"),
    inputs = list(x = as.double(x), y = lapply(y, as.double), model = model,
                  x_is_log = scalar(x_is_log), x_min = scalar(as.double(x_min)),
                  x_max = scalar(as.double(x_max)), at = as.double(at)),
    expected = out,
    kinds = kinds,
    packages = c("stats", "drc", "minpack.lm")
  )
}

simulate <- function(conc, p, sd, reps) {
  lapply(seq_len(reps), function(i) ll4(p, log10(conc)) + rnorm(length(conc), sd = sd))
}

# --- Cases ---------------------------------------------------------------------------

conc <- 10^seq(-9, -4, by = 0.5)            # molar, 11 concentrations
at_conc <- 10^seq(-9, -4, by = 1)

rising <- c(bottom = 5, top = 95, log_ec50 = -6.5, hill_slope = 1.2)
y_rising <- simulate(conc, rising, 4, 3)
falling <- c(bottom = 10, top = 100, log_ec50 = -7, hill_slope = -0.8)
y_falling <- simulate(conc, falling, 5, 2)
rising0 <- c(bottom = 0, top = 120, log_ec50 = -6, hill_slope = 1)
y_rising0 <- simulate(conc, rising0, 5, 3)
falling0 <- c(bottom = 0, top = 80, log_ec50 = -7.2, hill_slope = -1.5)
y_falling0 <- simulate(conc, falling0, 4, 2)

conc_um <- c(0, 0.003, 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300)  # with a control
p_um <- c(bottom = 2, top = 50, log_ec50 = 0, hill_slope = 0.9)
y_um <- list(ll4(p_um, log10(conc_um)) + rnorm(12, sd = 2.5))

for (model in c("4pl", "3pl")) {
  y_r <- if (model == "4pl") y_rising else y_rising0
  y_f <- if (model == "4pl") y_falling else y_falling0
  logistic_fixture("rising", "11 concentrations, 3 replicates, rising curve.",
                   conc, y_r, model, at_conc)
  logistic_fixture("falling", "11 concentrations, 2 replicates, falling curve (negative slope).",
                   conc, y_f, model, at_conc)
  logistic_fixture("log_x", "The rising data with X entered as log10(concentration).",
                   log10(conc), y_r, model, seq(-9, -4, by = 1), x_is_log = TRUE)
  logistic_fixture("zero_control", "12 concentrations including a 0 control, one replicate.",
                   conc_um, y_um, model, c(0, 0.01, 1, 100))
  logistic_fixture("x_range", "The rising data fitted on 1e-8 <= X <= 1e-5 only.",
                   conc, y_r, model, at_conc, x_min = 1e-8, x_max = 1e-5)
  y_m <- y_r
  y_m[[1]][c(2, 7)] <- NaN
  y_m[[2]][c(7, 11)] <- NaN
  y_m[[3]][5] <- NaN
  x_m <- conc
  x_m[4] <- NaN
  logistic_fixture("missing", "Missing X and Y values: unbalanced replicates per row.",
                   x_m, y_m, model, at_conc)
  logistic_fixture("ties", "Responses rounded to multiples of 10: many tied values.",
                   conc, lapply(y_r, function(v) round(v / 10) * 10), model, at_conc)
  logistic_fixture("n_small", "Six concentrations, one replicate: df = 2 (4PL) or 3 (3PL).",
                   conc[c(1, 3, 5, 7, 9, 11)], list(y_r[[1]][c(1, 3, 5, 7, 9, 11)]),
                   model, at_conc)
  logistic_fixture("n_two", "Two points: fewer than the parameters, no fit.",
                   conc[c(3, 9)], list(c(10, 90)), model, at_conc, expect_fit = FALSE)
  logistic_fixture("constant", "Every response is 42: the EC50 and slope are not determined.",
                   conc, list(rep(42, 11), rep(42, 11)), model, at_conc, expect_fit = FALSE)
}

steep <- c(bottom = 0, top = 100, log_ec50 = -6, hill_slope = 3.5)
logistic_fixture("steep", "A steep curve (Hill slope 3.5) with points on both plateaus.",
                 10^seq(-8, -4, by = 0.25), simulate(10^seq(-8, -4, by = 0.25), steep, 3, 2),
                 "4pl", 10^seq(-8, -4, by = 1))

# An exact straight line in log(concentration): a 4PL gets closer to it as both plateaus
# move away and the slope flattens, so the least-squares minimum is at infinity. (With
# noise added, the noise's own curvature can give a finite minimum far outside the data.)
logistic_fixture("no_plateaus",
                 "An exact straight line in log(concentration): the 4PL fit has no minimum.",
                 conc, list(50 + 8 * (log10(conc) + 6.5)), "4pl", at_conc, expect_fit = FALSE)

d <- .drc_se$diff
message(sprintf("  dose_response: Hill slope SE vs. drc's Hessian-based vcov, rel. diff median %.1e, max %.1e (%d fits)",
                median(d), max(d), length(d)))
