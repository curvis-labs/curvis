# Exact P values and critical values of Tukey's studentized range and Dunnett's test,
# shared by the ANOVA scripts that came after one_way_anova.R (which keeps its own copy
# of the same functions, unchanged since it was reviewed).
#
#   source(file.path(r_project_dir(), "R", "posthoc.R"), local = TRUE)
#
# Why exact integrals rather than ptukey/qtukey or multcomp, and the integrals
# themselves: see the header of scripts/one_way_anova.R. In short, ptukey has relative
# error up to ~7e-3 at small df and multcomp's P values vary with the random seed by up
# to ~1%; these integrals are computed with stats::integrate to a relative tolerance
# of 1e-10.

REL_TOL <- 1e-10

# Density of s = chi(df) / sqrt(df).
chi_scaled_density <- function(s, df) {
  out <- numeric(length(s))
  pos <- s > 0
  out[pos] <- exp(log(2) + (df / 2) * log(df / 2) - lgamma(df / 2) +
                  (df - 1) * log(s[pos]) - df * s[pos]^2 / 2)
  out
}

# Integral over z of f(z) on the real line, split at 0 (f is vectorized in z).
integrate_line <- function(f) {
  i <- function(lo, hi) integrate(f, lo, hi, rel.tol = REL_TOL, abs.tol = 0,
                                  subdivisions = 1000L)$value
  i(-Inf, 0) + i(0, Inf)
}

# E_s[g(s)] for s = chi(df) / sqrt(df), split at the mode of the density.
expect_over_s <- function(g, df) {
  mode <- sqrt((df - 1) / df)
  f <- function(s) chi_scaled_density(s, df) * vapply(s, g, numeric(1))
  i <- function(lo, hi) integrate(f, lo, hi, rel.tol = REL_TOL, abs.tol = 0,
                                  subdivisions = 1000L)$value
  if (mode > 0) i(0, mode) + i(mode, Inf) else i(0, Inf)
}

# a^m - (a - b)^m for 0 <= b <= a, without cancellation.
power_difference <- function(a, b, m) {
  out <- numeric(length(a))
  pos <- a > 0
  out[pos] <- a[pos]^m * -expm1(m * log1p(-b[pos] / a[pos]))
  out
}

# P(Q > q) for the studentized range of k means with df degrees of freedom.
tukey_upper <- function(q, k, df) {
  if (q <= 0) return(1)
  inner <- function(s) {
    w <- q * s
    k * integrate_line(function(z) dnorm(z) * power_difference(pnorm(z), pnorm(z - w),
                                                               k - 1))
  }
  expect_over_s(inner, df)
}

# P(max_j |T_j| >= c) for Dunnett's comparisons with the given lambda_j.
dunnett_upper <- function(c, lambda, df) {
  if (c <= 0) return(1)
  a <- sqrt(1 - lambda^2)
  inner <- function(s) {
    integrate_line(function(z) {
      # P(outside) for each z (rows) and comparison (columns), from both upper tails.
      lz <- outer(z, lambda)
      cs <- matrix(c * s / a, length(z), length(lambda), byrow = TRUE)
      sa <- matrix(a, length(z), length(lambda), byrow = TRUE)
      r <- pnorm(cs - lz / sa, lower.tail = FALSE) + pnorm(cs + lz / sa, lower.tail = FALSE)
      dnorm(z) * -expm1(rowSums(log1p(-pmin(r, 1))))
    })
  }
  expect_over_s(inner, df)
}

# The value x with upper(x) = alpha (upper is decreasing).
critical_value <- function(upper, alpha = 0.05) {
  uniroot(function(x) upper(x) - alpha, c(0.5, 50), tol = 1e-13)$root
}

