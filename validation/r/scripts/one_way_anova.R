# Ordinary one-way ANOVA and its post-hoc tests: Tukey's HSD (Tukey-Kramer for unequal
# n), Dunnett's test against a chosen control, and pairwise t tests with Holm's
# correction.
#
# Curvis: curvis.analysis.compare.anova and curvis.analysis.compare.posthoc.
#
# Each case writes four fixtures: one_way_anova, tukey_hsd, dunnett and holm_pairwise.
#
# Definitions:
# - Groups are the input vectors in order; missing values (NaN) are left out.
#   Group indices in the fixtures start at 0.
# - ANOVA table: between-groups and within-groups (residual) df, SS and MS,
#   F = MS_between / MS_within and its P value, checked against stats::aov. The SS are
#   computed from the group means (see anova_case), so that equal means give exactly 0. With no variation within groups
#   (SS_within = 0) F and P are not defined and are NaN (aov would divide by 0).
# - All post-hoc tests use the ANOVA's pooled error (MS_within, df_within). When
#   SS_within = 0 their test statistics, P values and CIs are NaN.
# - Pairs are in the order of TukeyHSD and pairwise.t.test: (0,1), (0,2), ..., (1,2), ...
#   and the difference of pair (i, j) is mean_j - mean_i.
# - Tukey: q = |difference| / sqrt(MS_within / 2 * (1/n_i + 1/n_j)); P = P(Q > q) for the
#   studentized range Q with k groups and df_within; the 95% CI is
#   difference -/+ q_crit * sqrt(MS_within / 2 * (1/n_i + 1/n_j)) with P(Q > q_crit) = 0.05.
# - Dunnett (two-sided): t_i = (mean_i - mean_control) / SE_i with
#   SE_i = sqrt(MS_within * (1/n_i + 1/n_control)); P = P(max_j |T_j| >= |t_i|) for the
#   multivariate t of the k - 1 comparisons; the 95% CI is
#   difference -/+ c * SE_i with P(max_j |T_j| >= c) = 0.05.
# - Holm: t = difference / sqrt(MS_within * (1/n_i + 1/n_j)) on df_within, two-sided P,
#   adjusted with p.adjust(method = "holm") over all pairs (pairwise.t.test with
#   pool.sd = TRUE).
#
# Tukey and Dunnett P values and critical values: exact integrals, not ptukey/qtukey or
# multcomp. Both R methods are approximations:
# - ptukey loses accuracy for small df (relative error up to ~7e-3 at df = 2, checked
#   against the exact identity P(Q > q) = P(|T| > q / sqrt(2)) for k = 2), and qtukey is
#   accurate to ~1e-4.
# - multcomp (via mvtnorm) uses randomized quasi-Monte Carlo integration: its P values
#   change with the random seed by up to ~1% relative, with a reported error ~5e-4.
# So the expected values come from the defining integrals, computed with
# stats::integrate to a relative tolerance of 1e-10, and the script checks them against
# TukeyHSD and multcomp::glht (within those methods' own accuracy). The integrals:
# - s = chi(df) / sqrt(df) has density 2 (df/2)^(df/2) / Gamma(df/2) s^(df-1) exp(-df s^2 / 2).
# - Studentized range: P(Q > q) = E_s[ k * integral phi(z) (Phi(z)^(k-1)
#   - (Phi(z) - Phi(z - q s))^(k-1)) dz ].
# - Dunnett: with lambda_j = sqrt(n_j / (n_j + n_control)), the comparisons are
#   T_j = (lambda_j Z + sqrt(1 - lambda_j^2) E_j) / s for independent standard normal
#   Z, E_j (Dunnett 1955), so P(max |T_j| >= c) = E_s[ integral phi(z)
#   (1 - prod_j P(|lambda_j z + sqrt(1 - lambda_j^2) E_j| < c s)) dz ].
# Upper tails are computed directly (not as 1 - CDF) so small P values keep their
# relative accuracy.

suppressPackageStartupMessages(library(multcomp))

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

anova_case <- function(case, description, groups, control = 0L) {
  groups <- lapply(groups, as.double)
  k <- length(groups)
  stopifnot(k >= 2L, control >= 0L, control < k)
  kept <- lapply(groups, function(g) g[!is.na(g)])
  n <- lengths(kept)
  stopifnot(all(n >= 1L))
  means <- vapply(kept, mean, numeric(1))
  y <- unlist(kept)
  g <- factor(rep(seq_len(k), n), levels = seq_len(k))
  fit <- aov(y ~ g)
  tab <- summary(fit)[[1]]
  df_b <- k - 1L
  df_w <- length(y) - k
  stopifnot(tab$Df[1] == df_b, (df_w == 0L && nrow(tab) == 1L) || tab$Df[2] == df_w)
  # Sums of squares from the group means (aov's come from a QR fit, which leaves
  # rounding noise such as SS_between = 1e-31 for groups with equal means):
  # SS_between = sum n_i (mean_i - grand mean)^2, exactly 0 when all means are equal;
  # SS_within = sum over groups of sum (y - mean_i)^2.
  grand <- mean(y)
  ss_b <- if (all(means == means[1])) 0 else sum(n * (means - grand)^2)
  ss_w <- sum(vapply(kept, function(v) sum((v - mean(v))^2), numeric(1)))
  ss_scale <- ss_b + ss_w
  # aov and the direct sums agree to ~1e-9 with a large offset (rounding of the means).
  stopifnot(abs(tab[["Sum Sq"]][1] - ss_b) <= 1e-8 * ss_scale,
            df_w == 0L || abs(tab[["Sum Sq"]][2] - ss_w) <= 1e-8 * ss_scale)
  ms_b <- ss_b / df_b
  ms_w <- if (df_w > 0L) ss_w / df_w else NaN
  defined <- df_w > 0L && ss_w > 0
  f <- if (defined) ms_b / ms_w else NaN
  p <- if (defined) pf(f, df_b, df_w, lower.tail = FALSE) else NaN
  if (defined && ss_b > 1e-6 * ss_scale) {
    stopifnot(isTRUE(all.equal(f, tab[["F value"]][1], tolerance = 1e-8)),
              isTRUE(all.equal(p, tab[["Pr(>F)"]][1], tolerance = 1e-8)))
  }
  inputs <- list(groups = groups, control = scalar(as.integer(control)))

  write_fixture(
    "one_way_anova", case, description, inputs,
    expected = list(n = as.integer(n), mean = means,
                    df_between = scalar(as.integer(df_b)),
                    df_within = scalar(as.integer(df_w)),
                    ss_between = scalar(ss_b), ss_within = scalar(ss_w),
                    ms_between = scalar(ms_b), ms_within = scalar(ms_w),
                    f = scalar(f), p_value = scalar(p)),
    kinds = c(n = "exact", mean = "statistic", df_between = "exact", df_within = "exact",
              ss_between = "statistic", ss_within = "statistic", ms_between = "statistic",
              ms_within = "statistic", f = "statistic", p_value = "p_value"),
    packages = "stats"
  )

  # Pairs (i, j), i < j, in TukeyHSD order (0-based).
  pairs <- do.call(rbind, lapply(seq_len(k - 1L), function(i)
    cbind(i, (i + 1L):k)))
  pi <- pairs[, 1]
  pj <- pairs[, 2]
  diff <- means[pj] - means[pi]

  # --- Tukey ---------------------------------------------------------------------
  half_se <- sqrt(ms_w / 2 * (1 / n[pi] + 1 / n[pj]))
  if (defined) {
    q <- abs(diff) / half_se
    tukey_p <- vapply(q, tukey_upper, numeric(1), k = k, df = df_w)
    q_crit <- critical_value(function(x) tukey_upper(x, k, df_w))
    lwr <- diff - q_crit * half_se
    upr <- diff + q_crit * half_se
    hsd <- TukeyHSD(fit, conf.level = 0.95)$g
    # TukeyHSD takes the differences from the model coefficients, which carry rounding
    # error relative to the size of the means.
    scale <- max(abs(means))
    stopifnot(isTRUE(all.equal(unname(hsd[, "diff"]), diff, tolerance = 1e-12,
                               scale = scale)))
    # ptukey's own accuracy (see above); report how far it is. Below P = 1e-6 ptukey can
    # be wrong by orders of magnitude, so those are not compared.
    usable <- tukey_p >= 1e-6
    rel <- max(c(0, abs(hsd[usable, "p adj"] - tukey_p[usable]) / tukey_p[usable]))
    rel_ci <- max(abs(c(hsd[, "lwr"] - lwr, hsd[, "upr"] - upr)) /
                  pmax(abs(c(lwr, upr)), 1e-12))
    message(sprintf("  tukey_hsd/%s: ptukey rel. diff %.1e, CI rel. diff %.1e",
                    case, rel, rel_ci))
    stopifnot(rel < 1e-2, rel_ci < 1e-2)
  } else {
    q <- tukey_p <- lwr <- upr <- rep(NaN, length(diff))
  }
  write_fixture(
    "tukey_hsd", case, description, inputs,
    expected = list(group_i = as.integer(pi - 1L), group_j = as.integer(pj - 1L),
                    difference = diff, q = q, p_value = tukey_p,
                    ci_lower = lwr, ci_upper = upr),
    kinds = c(group_i = "exact", group_j = "exact", difference = "statistic",
              q = "statistic", p_value = "p_value", ci_lower = "ci", ci_upper = "ci"),
    packages = "stats"
  )

  # --- Dunnett -------------------------------------------------------------------
  c0 <- control + 1L
  others <- setdiff(seq_len(k), c0)
  d_diff <- means[others] - means[c0]
  d_se <- sqrt(ms_w * (1 / n[others] + 1 / n[c0]))
  if (defined) {
    d_t <- d_diff / d_se
    lambda <- sqrt(n[others] / (n[others] + n[c0]))
    d_p <- vapply(abs(d_t), dunnett_upper, numeric(1), lambda = lambda, df = df_w)
    crit <- critical_value(function(x) dunnett_upper(x, lambda, df_w))
    d_lwr <- d_diff - crit * d_se
    d_upr <- d_diff + crit * d_se
    # Cross-check with multcomp (randomized QMC: within its reported error).
    g2 <- relevel(g, ref = as.character(c0))
    fit2 <- aov(y ~ g2)
    set.seed(1L)
    gl <- glht(fit2, linfct = mcp(g2 = "Dunnett"))
    sm <- summary(gl, test = adjusted("single-step"))
    order_mc <- match(as.character(others), levels(g2)[-1])
    est <- unname(sm$test$coefficients)[order_mc]
    stopifnot(isTRUE(all.equal(est, d_diff, tolerance = 1e-12, scale = max(abs(means)))),
              isTRUE(all.equal(unname(sm$test$sigma)[order_mc], d_se, tolerance = 1e-8)))
    err <- attr(sm$test$pvalues, "error")
    mc_p <- unname(as.numeric(sm$test$pvalues))[order_mc]
    # Allowed: 3 times multcomp's integration error, rounding in its t statistics (from
    # the model fit; ~1e-9 relative with a large offset), and a 1e-12 floor (mvtnorm
    # returns 0 for P values below its resolution).
    stopifnot(all(abs(mc_p - d_p) <= 3 * err + 1e-7 * d_p + 1e-12))
    set.seed(1L)
    calpha <- attr(confint(gl, level = 0.95)$confint, "calpha")
    rel_c <- abs(calpha - crit) / crit
    message(sprintf(paste("  dunnett/%s: multcomp P within %.1e (its error %.1e),",
                          "critical value rel. diff %.1e"),
                    case, max(abs(mc_p - d_p)), err, rel_c))
    stopifnot(rel_c < 1e-2)
  } else {
    d_t <- d_p <- d_lwr <- d_upr <- rep(NaN, length(d_diff))
  }
  write_fixture(
    "dunnett", case, description, inputs,
    expected = list(group = as.integer(others - 1L), difference = d_diff, se = d_se,
                    t = d_t, p_value = d_p, ci_lower = d_lwr, ci_upper = d_upr),
    kinds = c(group = "exact", difference = "statistic", se = "statistic",
              t = "statistic", p_value = "p_value", ci_lower = "ci", ci_upper = "ci"),
    packages = c("stats", "multcomp", "mvtnorm")
  )

  # --- Holm ----------------------------------------------------------------------
  h_se <- sqrt(ms_w * (1 / n[pi] + 1 / n[pj]))
  if (defined) {
    h_t <- diff / h_se
    raw <- 2 * pt(abs(h_t), df_w, lower.tail = FALSE)
    adj <- p.adjust(raw, method = "holm")
    pw_raw <- pairwise.t.test(y, g, p.adjust.method = "none", pool.sd = TRUE)$p.value
    pw_adj <- pairwise.t.test(y, g, p.adjust.method = "holm", pool.sd = TRUE)$p.value
    # pairwise.t.test pools per-group SDs, a different rounding path: with a large
    # offset the two agree to ~1e-9 relative, not to the last digit.
    stopifnot(isTRUE(all.equal(pw_raw[cbind(pj - 1L, pi)], raw, tolerance = 1e-8)),
              isTRUE(all.equal(pw_adj[cbind(pj - 1L, pi)], adj, tolerance = 1e-8)))
  } else {
    h_t <- raw <- adj <- rep(NaN, length(diff))
  }
  write_fixture(
    "holm_pairwise", case, description, inputs,
    expected = list(group_i = as.integer(pi - 1L), group_j = as.integer(pj - 1L),
                    difference = diff, se = h_se, t = h_t,
                    df = scalar(as.integer(df_w)),
                    p_unadjusted = raw, p_value = adj),
    kinds = c(group_i = "exact", group_j = "exact", difference = "statistic",
              se = "statistic", t = "statistic", df = "exact",
              p_unadjusted = "p_value", p_value = "p_value"),
    packages = "stats"
  )
}

# --- Cases -------------------------------------------------------------------------

anova_case("basic", "Four normal groups of 6, 5, 7 and 6 values.",
           list(rnorm(6, 10, 2), rnorm(5, 12, 2), rnorm(7, 15, 2), rnorm(6, 11, 2)))

anova_case("n_small", "Three groups of n = 2 (df_within = 3).",
           list(c(1.1, 2.3), c(3.5, 4.1), c(2.0, 6.2)))

anova_case("ties", "Repeated values within and across groups.",
           list(c(3, 3, 4, 4, 5), c(4, 4, 5, 5, 5), c(5, 6, 6, 6, 7)))

anova_case("missing", "Missing values in every group.",
           list(c(5.2, NaN, 6.1, 5.8, 4.9, NaN), c(NaN, 7.0, 6.4, 7.7, 6.9, 7.2),
                c(5.5, 5.9, NaN, NaN, 6.3, 5.0)))

anova_case("unbalanced", "Groups of 3, 12, 5 and 30 values with different SDs.",
           list(rnorm(3, 50, 5), rnorm(12, 55, 8), rnorm(5, 47, 3), rnorm(30, 52, 10)))

anova_case("identical", "Three groups with the same values: F = 0, P = 1.",
           rep(list(c(2.5, 3.1, 4.7, 3.9)), 3))

anova_case("zero_variance_one", "One group is constant, the others are not.",
           list(c(4, 4, 4, 4), c(5.1, 6.3, 5.8, 6.6), c(3.9, 4.8, 5.2, 4.4)))

anova_case("constant_all", "Every group is constant (different values): no variation within groups.",
           list(c(1, 1, 1), c(2, 2, 2), c(4, 4, 4)))

anova_case("two_groups", "Two groups: F = t^2 of Student's t test.",
           list(rnorm(8, 0, 1), rnorm(9, 1, 1)))

anova_case("many_groups", "Eight groups of 4 to 9 values (seven Dunnett comparisons).",
           lapply(c(4, 6, 9, 5, 7, 8, 4, 6), function(m) rnorm(m, mean = m, sd = 2)))

anova_case("control_not_first", "Dunnett's control is the third group.",
           list(rnorm(5, 20, 3), rnorm(6, 24, 3), rnorm(5, 19, 3), rnorm(7, 26, 3)),
           control = 2L)

anova_case("tiny_p", "Large differences between 20-value groups: very small P values.",
           list(rnorm(20, 0, 1), rnorm(20, 2, 1), rnorm(20, 4, 1)))

anova_case("large_offset", "Small spread on a large offset.",
           list(1e6 + c(0.12, 0.31, 0.25, 0.18), 1e6 + c(0.42, 0.37, 0.55, 0.49),
                1e6 + c(0.20, 0.33, 0.29, 0.41)))

anova_case("large_df", "Five groups of 60: df_within = 295.",
           lapply(c(0, 0.2, 0.5, 0.1, 0.4), function(m) rnorm(60, mean = m, sd = 1)))
