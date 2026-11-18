# Shared code for the fixtures of the nonlinear curve fits of step 3.3 (biphasic,
# exponential association and decay, Michaelis-Menten, bell-shaped, Gaussian). The
# equations are in docs/models.md. dose_response.R (4PL, 3PL) predates this file and
# keeps its own copy of the same method.
#
# A model is a list:
#   name     analysis name of its fixtures (and of the Python adapter)
#   params   parameter names, in the order of docs/models.md
#   log_x    TRUE when the model is written on X' = log10(X)
#   f        function(p, x): the curve (p a named vector of every parameter)
#   grad     function(p, x): d f / d p, a matrix with one named column per parameter
#   canonical function(p, free): the same curve in its reported form; `free` names the
#            parameters that are free (only those may change form)
#   derived  list of derived values (see derived_values())
#
# Method (as dose_response.R):
# - Points: every replicate value is its own point, paired with the X of its row; a
#   point with a missing X or Y is left out, and so is one outside [x_min, x_max] (in
#   the units of the X column). For a log_x model with x_is_log = FALSE, X' = log10(X),
#   X = 0 is allowed (X' = -Inf, the curve's limit) and a negative X is left out.
# - Fit: unweighted least squares with minpack.lm::nlsLM (ftol = ptol = 1e-15), from
#   the true parameters and from 24 random perturbations of them, so the reference
#   never depends on Curvis' automatic initial values. The lowest RSS wins; Gauss-Newton
#   steps with the analytic Jacobian finish it (polish()), and the script checks that
#   it is a minimum: the residuals are orthogonal to every column of the Jacobian
#   (cosine < 1e-7).
# - SE: asymptotic, sqrt(diag(s² (JᵀJ)⁻¹)), J the analytic Jacobian at the optimum in
#   the free parameters, s² = RSS / df, df = n - number of free parameters. Checked
#   against vcov() of the winning nlsLM fit (numerical Jacobian) to 1e-3.
# - CI: estimate -/+ qt(0.975, df) * SE. R² = 1 - RSS / TSS; Sy.x = sqrt(RSS / df).
# - Band at `at` (units of the X column): fitted value -/+ qt(0.975, df) * sqrt(g V gᵀ),
#   g the gradient in the free parameters (delta method).
#
# No fit (fit_ok = false; every output except n is NaN):
# - Fewer points than free parameters.
# - No start converges (nlsLM fails or reports no convergence), or the optimum does not
#   determine the parameters: JᵀJ numerically singular (reciprocal condition number of
#   the column-scaled JᵀJ < 1e-12, or a column of J is 0), or two parameters correlated
#   with |r| > 0.9999. Cases meant to fail are marked expect_fit = FALSE and the script
#   checks that they fail.
# A fit with df = 0 has no SE, CI, Sy.x or band limits (NaN).

LN10 <- log(10)

# 1 / (1 + 10^z) and its derivative in z; exact limits at z = -/+Inf.
logistic_g <- function(z) 1 / (1 + 10^z)
logistic_dg <- function(g) -g * (1 - g) * LN10

# Replaces NaN/Inf in a gradient with 0. Only arises at X' = -Inf, where the terms are
# (X' - L) times a weight that has underflowed to 0; the limit is 0.
finite_or_zero <- function(m) {
  m[!is.finite(m)] <- 0
  m
}

# Nonlinear fit: list(ok = FALSE, reason) when there is no fit, else
# list(ok = TRUE, p, free, rss, df, inv, n, starts_ok).
# `starts` is a list of named vectors of every parameter; `fixed` a named vector.
nl_fit <- function(model, x, y, starts, fixed = numeric()) {
  free <- setdiff(model$params, names(fixed))
  n <- length(y)
  if (n < length(free)) return(no_fit("fewer points than free parameters"))
  env <- new.env(parent = globalenv())
  env$.mv <- function(x, ...) {
    p <- full_params(model, free, c(...), fixed)
    model$f(p, x)
  }
  formula <- as.formula(paste0("y ~ .mv(x, ", paste(free, collapse = ", "), ")"), env = env)
  d <- data.frame(x = x, y = y)
  best <- NULL
  n_ok <- 0L
  for (s in starts) {
    fit <- tryCatch(
      suppressWarnings(minpack.lm::nlsLM(
        formula, data = d, start = as.list(s[free]),
        control = minpack.lm::nls.lm.control(ftol = 1e-15, ptol = 1e-15, maxiter = 1000))),
      error = function(e) NULL)
    if (is.null(fit) || !isTRUE(fit$convInfo$isConv)) next
    n_ok <- n_ok + 1L
    if (is.null(best) || deviance(fit) < deviance(best)) best <- fit
  }
  if (is.null(best)) return(no_fit(sprintf("none of the %d starts converged", length(starts))))
  p <- full_params(model, free, coef(best)[free], fixed)
  p <- polish(model, p, free, x, y)
  r <- y - model$f(p, x)
  rss <- sum(r^2)
  jac <- finite_or_zero(model$grad(p, x))[, free, drop = FALSE]
  norms <- sqrt(colSums(jac^2))
  if (any(norms == 0)) return(no_fit(paste("no effect on the curve:", paste(free[norms == 0], collapse = ", ")), p))
  js <- sweep(jac, 2, norms, "/")
  jtj_s <- crossprod(js)
  if (rcond(jtj_s) < 1e-12) return(no_fit(sprintf("JtJ singular (rcond %.1e)", rcond(jtj_s)), p))
  inv <- scaled_inverse(jac)
  corr <- cov2cor(inv)
  if (length(free) > 1 && max(abs(corr[upper.tri(corr)])) > 0.9999) {
    return(no_fit(sprintf("max |r| = %.6f", max(abs(corr[upper.tri(corr)]))), p))
  }
  if (rss > 0) {
    cosine <- abs(crossprod(jac, r)) / (norms * sqrt(rss))
    if (max(cosine) > 1e-7) stop(model$name, ": not at the minimum (cosine ", max(cosine), ")")
  }
  df <- n - length(free)
  # SEs agree with nlsLM's own (numerical Jacobian at its own end point).
  if (df > 0) {
    se <- sqrt(diag(rss / df * inv))
    se_nls <- sqrt(diag(vcov(best)))[free]
    stopifnot(isTRUE(all.equal(unname(se), unname(se_nls), tolerance = 1e-3)))
  }
  # Report the canonical form; it is the same curve, so the RSS is unchanged.
  q <- model$canonical(p, free)
  stopifnot(isTRUE(all.equal(sum((y - model$f(q, x))^2), rss, tolerance = 1e-12)))
  if (!identical(q, p)) {
    jac <- finite_or_zero(model$grad(q, x))[, free, drop = FALSE]
    inv <- scaled_inverse(jac)
  }
  list(ok = TRUE, p = q, free = free, rss = rss, df = df, inv = inv, n = n, starts_ok = n_ok)
}

no_fit <- function(reason, p = NULL) {
  if (!is.null(p)) reason <- paste0(reason, " at ", paste(names(p), signif(p, 4), sep = " = ", collapse = ", "))
  list(ok = FALSE, reason = reason)
}

# (JᵀJ)⁻¹ through the column-scaled Js = J / norms (parameters can differ in scale by
# many orders of magnitude): (JᵀJ)⁻¹ = D⁻¹ (JsᵀJs)⁻¹ D⁻¹, D = diag(norms).
scaled_inverse <- function(jac) {
  norms <- sqrt(colSums(jac^2))
  js <- sweep(jac, 2, norms, "/")
  inv <- solve(crossprod(js)) / outer(norms, norms)
  dimnames(inv) <- list(colnames(jac), colnames(jac))
  inv
}

full_params <- function(model, free, values, fixed) {
  p <- setNames(double(length(model$params)), model$params)
  p[free] <- unname(values)
  if (length(fixed)) p[names(fixed)] <- fixed
  p
}

# Gauss-Newton steps with the analytic Jacobian, each halved until it lowers the RSS;
# they stop when a step no longer lowers it.
polish <- function(model, p, free, x, y) {
  rss <- sum((y - model$f(p, x))^2)
  for (i in 1:100) {
    jac <- finite_or_zero(model$grad(p, x))[, free, drop = FALSE]
    step <- tryCatch(qr.solve(jac, y - model$f(p, x)), error = function(e) NULL)
    if (is.null(step) || any(!is.finite(step))) break
    improved <- FALSE
    for (k in 0:30) {
      q <- p
      q[free] <- p[free] + step / 2^k
      rss_q <- sum((y - model$f(q, x))^2)
      if (is.finite(rss_q) && rss_q < rss) {
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

# Random perturbations of the true parameters, as nlsLM starts: each parameter moves by
# a normal step of `scale[name]` (absolute), or by a factor exp(N(0, 0.3)) for the
# names in `relative`. The true parameters themselves come first.
perturbed_starts <- function(truth, scale = numeric(), relative = character(), count = 24L) {
  starts <- list(truth)
  for (i in seq_len(count)) {
    s <- truth
    for (nm in names(scale)) s[[nm]] <- s[[nm]] + rnorm(1, sd = scale[[nm]])
    for (nm in relative) s[[nm]] <- s[[nm]] * exp(rnorm(1, sd = 0.3))
    starts[[i + 1L]] <- s
  }
  starts
}

# Derived values. Each entry of model$derived is a list with
#   name    output name
#   value   function(p) -> the value
#   either  linear: a named coefficient vector c (value is linear in the parameters):
#             SE = sqrt(cᵀ V c), CI = value -/+ q SE; outputs <name>, _se, _ci_lower,
#             _ci_upper. Needs every parameter with a nonzero coefficient to be free.
#   or      from: a parameter name and map: a monotone function of it; CI = map(CI of
#             the parameter), sorted; outputs <name>, _ci_lower, _ci_upper. For
#             `reciprocal = TRUE` (half-time, tau of K): defined only for K > 0, and when
#             the CI of K reaches 0 or below, the upper limit is Inf.
derived_names <- function(entry) {
  if (!is.null(entry$linear)) {
    paste0(entry$name, c("", "_se", "_ci_lower", "_ci_upper"))
  } else {
    paste0(entry$name, c("", "_ci_lower", "_ci_upper"))
  }
}

derived_values <- function(model, f, se, lower, upper, q) {
  out <- list()
  for (entry in model$derived) {
    nm <- entry$name
    if (!is.null(entry$linear)) {
      c_all <- setNames(double(length(model$params)), model$params)
      c_all[names(entry$linear)] <- entry$linear
      if (any(c_all[setdiff(model$params, f$free)] != 0)) next
      cf <- c_all[f$free]
      out[[nm]] <- entry$value(f$p)
      if (f$df > 0) {
        s <- sqrt(drop(t(cf) %*% (f$rss / f$df * f$inv) %*% cf))
        out[[paste0(nm, "_se")]] <- s
        out[[paste0(nm, "_ci_lower")]] <- out[[nm]] - q * s
        out[[paste0(nm, "_ci_upper")]] <- out[[nm]] + q * s
      } else {
        out[[paste0(nm, "_se")]] <- NaN
        out[[paste0(nm, "_ci_lower")]] <- NaN
        out[[paste0(nm, "_ci_upper")]] <- NaN
      }
    } else {
      if (!(entry$from %in% f$free)) next
      v <- f$p[[entry$from]]
      lo <- lower[[entry$from]]
      hi <- upper[[entry$from]]
      if (isTRUE(entry$reciprocal)) {
        if (v <= 0) {
          out[[nm]] <- NaN
          lims <- c(NaN, NaN)
        } else {
          out[[nm]] <- entry$map(v)
          lims <- if (is.nan(lo)) c(NaN, NaN)
                  else if (lo <= 0) c(entry$map(hi), Inf)
                  else sort(c(entry$map(lo), entry$map(hi)))
        }
      } else {
        out[[nm]] <- entry$map(v)
        lims <- if (is.nan(lo)) c(NaN, NaN) else sort(c(entry$map(lo), entry$map(hi)))
      }
      out[[paste0(nm, "_ci_lower")]] <- lims[1]
      out[[paste0(nm, "_ci_upper")]] <- lims[2]
    }
  }
  out
}

derived_kinds <- function(entry) {
  if (!is.null(entry$linear)) {
    k <- c("parameter", "parameter", "ci", "ci")
  } else {
    k <- c("parameter", "ci", "ci")
  }
  setNames(k, derived_names(entry))
}

# Optional cross-check against another R implementation: function(x, y, fixed) that
# returns a named vector of the parameters (any subset), NULL when it does not apply,
# or an error when the other method fails (counted, not fatal: e.g. a self-start
# that cannot find its own initial values).
.crosscheck <- new.env()
.crosscheck$count <- 0L
.crosscheck$failed <- 0L
.crosscheck$reasons <- character()

# coef() of stats::nls from `start` with the tightest relative-offset tolerance it
# reaches (1e-10, else 1e-9, else 1e-8: from a start already at the minimum, rounding
# can keep it from the tightest); an error when none converges.
tight_nls <- function(formula, data, start) {
  for (tol in c(1e-10, 1e-9, 1e-8)) {
    fit <- tryCatch(nls(formula, data = data, start = start,
                        control = nls.control(tol = tol, maxiter = 500)),
                    error = function(e) NULL)
    if (!is.null(fit)) return(coef(fit))
  }
  stop("nls did not converge with tol <= 1e-8")
}

# Writes one fixture. `truth` (named vector of every parameter) seeds the starts; it
# need not be the data's true curve (for cases without one, it is a sensible start).
nl_fixture <- function(model, case, description, x, y, at, truth, fixed = numeric(),
                       x_is_log = FALSE, x_min = -Inf, x_max = Inf, expect_fit = TRUE,
                       scale = numeric(), relative = character(), crosscheck = NULL) {
  stopifnot(is.list(y), all(lengths(y) == length(x)), setequal(names(truth), model$params),
            all(names(fixed) %in% model$params))
  truth <- truth[model$params]
  xs <- rep(as.double(x), times = length(y))
  ys <- unlist(lapply(y, as.double))
  keep <- !is.na(xs) & !is.na(ys) & xs >= x_min & xs <= x_max
  if (model$log_x && !x_is_log) keep <- keep & xs >= 0
  xf <- if (model$log_x && !x_is_log) log10(xs[keep]) else xs[keep]
  ys <- ys[keep]
  at_f <- if (model$log_x && !x_is_log) log10(as.double(at)) else as.double(at)

  free <- setdiff(model$params, names(fixed))
  out <- list(fit_ok = FALSE, n = length(ys), df = NaN)
  kinds <- c(fit_ok = "exact", n = "exact", df = "exact")
  for (nm in free) {
    for (suffix in c("", "_se", "_ci_lower", "_ci_upper")) out[[paste0(nm, suffix)]] <- NaN
    kinds[paste0(nm, c("", "_se", "_ci_lower", "_ci_upper"))] <-
      c("parameter", "parameter", "ci", "ci")
  }
  for (entry in model$derived) {
    usable <- if (!is.null(entry$linear)) all(names(entry$linear)[entry$linear != 0] %in% free)
              else entry$from %in% free
    if (!usable) next
    for (nm in derived_names(entry)) out[[nm]] <- NaN
    kinds <- c(kinds, derived_kinds(entry))
  }
  out <- c(out, list(r_squared = NaN, sy_x = NaN, band_fit = rep(NaN, length(at)),
                     band_lower = rep(NaN, length(at)), band_upper = rep(NaN, length(at))))
  kinds <- c(kinds, r_squared = "statistic", sy_x = "parameter", band_fit = "parameter",
             band_lower = "ci", band_upper = "ci")

  starts <- perturbed_starts(truth, scale, relative)
  for (i in seq_along(starts)) starts[[i]][names(fixed)] <- fixed
  f <- nl_fit(model, xf, ys, starts, fixed)
  if (!f$ok) message(sprintf("  %s/%s: no fit (%s)", model$name, case, f$reason))
  if (f$ok) {
    out$fit_ok <- TRUE
    out$df <- f$df
    out$r_squared <- 1 - f$rss / sum((ys - mean(ys))^2)
    fitted_at <- model$f(f$p, at_f)
    out$band_fit <- fitted_at
    se <- lower <- upper <- setNames(rep(NaN, length(free)), free)
    q <- if (f$df > 0) qt(0.975, f$df) else NaN
    if (f$df > 0) {
      v <- f$rss / f$df * f$inv
      se <- setNames(sqrt(diag(v)), free)
      lower <- f$p[free] - q * se
      upper <- f$p[free] + q * se
      out$sy_x <- sqrt(f$rss / f$df)
      g <- finite_or_zero(model$grad(f$p, at_f))[, free, drop = FALSE]
      se_fit <- sqrt(rowSums((g %*% v) * g))
      out$band_lower <- fitted_at - q * se_fit
      out$band_upper <- fitted_at + q * se_fit
    }
    for (nm in free) {
      out[[nm]] <- f$p[[nm]]
      out[[paste0(nm, "_se")]] <- se[[nm]]
      out[[paste0(nm, "_ci_lower")]] <- lower[[nm]]
      out[[paste0(nm, "_ci_upper")]] <- upper[[nm]]
    }
    d <- derived_values(model, f, se, lower, upper, q)
    for (nm in names(d)) out[[nm]] <- d[[nm]]
    if (!is.null(crosscheck)) {
      other <- tryCatch(crosscheck(xf, ys, fixed), error = function(e) {
        .crosscheck$reasons <- c(.crosscheck$reasons, paste0(case, ": ", conditionMessage(e)))
        "failed"
      })
      if (identical(other, "failed")) {
        .crosscheck$count <- .crosscheck$count + 1L
        .crosscheck$failed <- .crosscheck$failed + 1L
      } else if (!is.null(other)) .crosscheck$count <- .crosscheck$count + 1L
      if (is.null(other) || identical(other, "failed")) {
        # not applicable, or the other method did not converge
      } else if (!isTRUE(all.equal(unname(other), unname(f$p[names(other)]),
                                   tolerance = 1e-6))) {
        stop(model$name, "/", case, ": the cross-check disagrees: ",
             paste(names(other), signif(other, 10), collapse = ", "))
      }
    }
  }
  if (out$fit_ok != expect_fit) stop(model$name, "/", case, ": fit_ok is ", out$fit_ok)
  out$n <- as.integer(out$n)

  inputs <- list(x = as.double(x), y = lapply(y, as.double))
  if (model$log_x) inputs$x_is_log <- scalar(x_is_log)
  inputs$fixed <- if (length(fixed)) lapply(as.list(fixed), function(v) scalar(as.double(v)))
                  else structure(list(), names = character())
  inputs <- c(inputs, list(x_min = scalar(as.double(x_min)), x_max = scalar(as.double(x_max)),
                           at = as.double(at)))
  write_fixture(
    analysis = model$name,
    case = case,
    description = description,
    inputs = inputs,
    expected = out,
    kinds = kinds[names(out)],
    packages = c("stats", "minpack.lm")
  )
  invisible(f)
}

# Prints the cross-check summary; called at the end of each script.
nl_summary <- function(model) {
  message(sprintf("  %s: %d cross-checks, %d of them could not run (other method failed)",
                  model$name, .crosscheck$count, .crosscheck$failed))
  for (r in .crosscheck$reasons) message("    ", substr(r, 1, 150))
}
