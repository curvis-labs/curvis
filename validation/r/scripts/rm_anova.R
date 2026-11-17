# Repeated-measures one-way ANOVA (rows are subjects) with the Greenhouse-Geisser
# correction, and its post-hoc tests: Tukey, Dunnett against a control, and pairwise t
# tests with Holm's correction.
#
# Curvis: curvis.analysis.compare.rm_anova.
#
# Each case writes four fixtures: rm_anova, rm_tukey, rm_dunnett and rm_holm.
#
# Definitions (decisions confirmed by the project owner for step 3.1):
# - Input: k treatments (columns), each a vector over the same n rows (subjects).
#   Missing values (NaN): a row with no values at all is ignored; a row with some but
#   not all values is an incomplete subject and is left out entirely (listwise), and
#   counted in n_incomplete. The analysis uses the n complete subjects.
# - Treatment indices in the fixtures start at 0.
# - ANOVA table with Y the n x k matrix of complete subjects and grand mean m:
#     SS_treatment = n sum_j (mean_j - m)^2   (exactly 0 when all column means are equal)
#     SS_subjects  = k sum_i (mean_i - m)^2
#     SS_error     = sum_ij (y_ij - mean_i - mean_j + m)^2
#   on k - 1, n - 1 and (n - 1)(k - 1) df; F = MS_treatment / MS_error. Checked against
#   stats::aov(y ~ treatment + Error(subject / treatment)). With SS_error = 0 F and P
#   are not defined and are NaN.
# - Greenhouse-Geisser: with S the k x k sample covariance of the columns and
#   C = I - J / k, S_c = C S C and epsilon = trace(S_c)^2 / ((k - 1) trace(S_c^2)),
#   which lies in [1 / (k - 1), 1] (exactly 1 when k = 2). The corrected test uses the
#   same F on epsilon (k - 1) and epsilon (n - 1)(k - 1) df. Checked against car::Anova
#   on the multivariate linear model (idata / idesign, type 3) when n - 1 >= k - 1 (car
#   reports no correction for fewer subjects). NaN when there is no residual variation.
#   Curvis applies the correction by default ("Assume sphericity" turns it off); both
#   P values are in the fixture.
# - Post-hoc tests use the pooled error of the ANOVA (MS_error on (n - 1)(k - 1) df) for
#   every comparison, as the univariate model does (emmeans on lm(y ~ treatment +
#   subject)); the Greenhouse-Geisser correction applies only to the main F test.
#   They are the one-way tests of one_way_anova.R with n per treatment and the RM
#   error: pairs (i, j), i < j, difference mean_j - mean_i, SE = sqrt(2 MS_error / n).
#   - Tukey: q = |difference| / sqrt(MS_error / n); P = P(Q > q) for k means.
#   - Dunnett (two-sided): t = difference / SE, lambda_j = sqrt(1/2) for every
#     comparison (equal n); 95% CI difference -/+ c SE.
#   - Holm: t = difference / SE, two-sided P on df_error, p.adjust(method = "holm").
#   Tukey and Dunnett P and critical values are the exact integrals in R/posthoc.R,
#   checked against emmeans and multcomp within those methods' accuracy.

suppressPackageStartupMessages({
  library(car)
  library(emmeans)
  library(multcomp)
})
source(file.path(r_project_dir(), "R", "posthoc.R"), local = TRUE)

rm_case <- function(case, description, treatments, control = 0L) {
  treatments <- lapply(treatments, as.double)
  k <- length(treatments)
  rows <- unique(lengths(treatments))
  stopifnot(k >= 2L, length(rows) == 1L, control >= 0L, control < k)
  full <- do.call(cbind, treatments)
  present <- !is.na(full)
  empty <- rowSums(present) == 0L
  complete <- rowSums(present) == k
  n_incomplete <- sum(!empty & !complete)
  Y <- full[complete, , drop = FALSE]
  n <- nrow(Y)
  stopifnot(n >= 2L)

  # Means refined with a second pass, as R's mean() does.
  col_means <- apply(Y, 2, mean)
  row_means <- apply(Y, 1, mean)
  grand <- mean(as.vector(Y))
  ss_t <- if (all(col_means == col_means[1])) 0 else n * sum((col_means - grand)^2)
  ss_s <- if (all(row_means == row_means[1])) 0 else k * sum((row_means - grand)^2)
  resid <- Y - outer(row_means, rep(1, k)) - outer(rep(1, n), col_means) + grand
  ss_e <- sum(resid^2)
  df_t <- k - 1L
  df_s <- n - 1L
  df_e <- df_t * df_s
  ms_t <- ss_t / df_t
  ms_s <- ss_s / df_s
  ms_e <- ss_e / df_e
  # Exactly additive data leave residuals of rounding size only; treat |resid| at the
  # level of rounding of the data as 0 (no variation left to test against).
  scale <- max(abs(Y))
  defined <- ss_e > (64 * .Machine$double.eps * scale)^2 * n * k
  if (!defined) ss_e <- 0
  ms_e <- ss_e / df_e
  f <- if (defined) ms_t / ms_e else NaN
  p <- if (defined) pf(f, df_t, df_e, lower.tail = FALSE) else NaN

  # Cross-check with aov and its Error strata.
  long <- data.frame(y = as.vector(Y),
                     subject = factor(rep(seq_len(n), k)),
                     treatment = factor(rep(seq_len(k), each = n)))
  strata <- summary(aov(y ~ treatment + Error(subject / treatment), data = long))
  within <- strata[["Error: subject:treatment"]][[1]]
  between <- strata[["Error: subject"]][[1]]
  # Allowed difference: relative to the total SS, with a floor at rounding level of the
  # data (aov's QR fit leaves ~1e-30 where the SS is exactly 0, e.g. constant data).
  ss_scale <- ss_t + ss_s + ss_e
  ss_tol <- 1e-8 * ss_scale + (64 * .Machine$double.eps * scale)^2 * n * k
  stopifnot(within$Df[1] == df_t, within$Df[2] == df_e, between$Df[1] == df_s,
            abs(within[["Sum Sq"]][1] - ss_t) <= ss_tol,
            abs(within[["Sum Sq"]][2] - ss_e) <= ss_tol,
            abs(between[["Sum Sq"]][1] - ss_s) <= ss_tol)
  if (defined && ss_t > 1e-6 * ss_scale) {
    stopifnot(isTRUE(all.equal(f, within[["F value"]][1], tolerance = 1e-8)),
              isTRUE(all.equal(p, within[["Pr(>F)"]][1], tolerance = 1e-8)))
  }

  # Greenhouse-Geisser epsilon.
  C <- diag(k) - matrix(1 / k, k, k)
  Sc <- C %*% cov(Y) %*% C
  tr2 <- sum(Sc * Sc)  # trace(Sc %*% Sc) for symmetric Sc
  # trace(S_c) = SS_error / (n - 1), so S_c is only rounding noise when there is no
  # residual variation: epsilon is then undefined.
  eps <- if (!defined) NaN else if (k == 2L) 1 else sum(diag(Sc))^2 / (df_t * tr2)
  stopifnot(is.nan(eps) || (eps >= 1 / df_t - 1e-12 && eps <= 1 + 1e-12))
  gg_df_t <- eps * df_t
  gg_df_e <- eps * df_e
  gg_p <- if (defined && !is.nan(eps)) pf(f, gg_df_t, gg_df_e, lower.tail = FALSE) else NaN
  # car needs a non-singular error SSP matrix (n - 1 >= k - 1); with fewer subjects
  # it reports no correction, and only the formula above is used.
  if (defined && k > 2L && !is.nan(eps) && df_s >= df_t) {
    idata <- data.frame(treatment = factor(seq_len(k)))
    mod <- lm(Y ~ 1)
    av <- car::Anova(mod, idata = idata, idesign = ~treatment, type = 3)
    s <- summary(av, multivariate = FALSE)
    car_eps <- s$pval.adjustments["treatment", "GG eps"]
    car_p <- s$pval.adjustments["treatment", "Pr(>F[GG])"]
    # car forms its SSP matrices from uncentered data, so with a large offset it is
    # accurate to ~1e-8 relative only (5e-10 for epsilon and 2e-8 for P in large_offset;
    # ~1e-15 otherwise). The formula above works on centered data.
    stopifnot(isTRUE(all.equal(eps, unname(car_eps), tolerance = 1e-6)))
    if (ss_t > 1e-6 * ss_scale) {
      stopifnot(isTRUE(all.equal(gg_p, unname(car_p), tolerance = 1e-6)))
    }
  }

  inputs <- list(treatments = treatments, control = scalar(as.integer(control)))
  write_fixture(
    "rm_anova", case, description, inputs,
    expected = list(n_subjects = scalar(as.integer(n)),
                    n_incomplete = scalar(as.integer(n_incomplete)),
                    mean = col_means,
                    df_treatment = scalar(as.integer(df_t)),
                    df_subjects = scalar(as.integer(df_s)),
                    df_error = scalar(as.integer(df_e)),
                    ss_treatment = scalar(ss_t), ss_subjects = scalar(ss_s),
                    ss_error = scalar(ss_e),
                    ms_treatment = scalar(ms_t), ms_subjects = scalar(ms_s),
                    ms_error = scalar(ms_e),
                    f = scalar(f), p_value = scalar(p),
                    gg_epsilon = scalar(eps), gg_df_treatment = scalar(gg_df_t),
                    gg_df_error = scalar(gg_df_e), gg_p_value = scalar(gg_p)),
    kinds = c(n_subjects = "exact", n_incomplete = "exact", mean = "statistic",
              df_treatment = "exact", df_subjects = "exact", df_error = "exact",
              ss_treatment = "statistic", ss_subjects = "statistic",
              ss_error = "statistic", ms_treatment = "statistic",
              ms_subjects = "statistic", ms_error = "statistic", f = "statistic",
              p_value = "p_value", gg_epsilon = "statistic",
              gg_df_treatment = "statistic", gg_df_error = "statistic",
              gg_p_value = "p_value"),
    packages = c("stats", "car")
  )

  pairs <- do.call(rbind, lapply(seq_len(k - 1L), function(i) cbind(i, (i + 1L):k)))
  pi <- pairs[, 1]
  pj <- pairs[, 2]
  diff <- col_means[pj] - col_means[pi]
  se <- rep(sqrt(2 * ms_e / n), length(diff))

  # The univariate model with subjects as a fixed factor gives the same pooled error.
  fit <- lm(y ~ treatment + subject, data = long)
  if (defined) {
    stopifnot(isTRUE(all.equal(sigma(fit)^2, ms_e, tolerance = 1e-8)))
  }

  # --- Tukey ---------------------------------------------------------------------
  if (defined) {
    q <- abs(diff) / (se / sqrt(2))
    tukey_p <- vapply(q, tukey_upper, numeric(1), k = k, df = df_e)
    q_crit <- critical_value(function(x) tukey_upper(x, k, df_e))
    lwr <- diff - q_crit * se / sqrt(2)
    upr <- diff + q_crit * se / sqrt(2)
    em <- summary(pairs(emmeans(fit, "treatment"), adjust = "tukey", reverse = TRUE),
                  infer = c(TRUE, TRUE))
    # emmeans' reverse pairs are (2 - 1), (3 - 1), (3 - 2), ...: put them in our order.
    lab <- paste0("treatment", pj, " - treatment", pi)
    m <- match(lab, as.character(em$contrast))
    stopifnot(!anyNA(m),
              isTRUE(all.equal(em$estimate[m], diff, tolerance = 1e-10,
                               scale = max(abs(col_means)))),
              isTRUE(all.equal(em$SE[m], se, tolerance = 1e-8)))
    usable <- tukey_p >= 1e-6
    rel <- max(c(0, abs(em$p.value[m][usable] - tukey_p[usable]) / tukey_p[usable]))
    message(sprintf("  rm_tukey/%s: emmeans (ptukey) P rel. diff %.1e", case, rel))
    stopifnot(rel < 1e-2)
  } else {
    q <- tukey_p <- lwr <- upr <- rep(NaN, length(diff))
  }
  write_fixture(
    "rm_tukey", case, description, inputs,
    expected = list(group_i = as.integer(pi - 1L), group_j = as.integer(pj - 1L),
                    difference = diff, se = se, q = q, p_value = tukey_p,
                    ci_lower = lwr, ci_upper = upr),
    kinds = c(group_i = "exact", group_j = "exact", difference = "statistic",
              se = "statistic", q = "statistic", p_value = "p_value",
              ci_lower = "ci", ci_upper = "ci"),
    packages = c("stats", "emmeans")
  )

  # --- Dunnett -------------------------------------------------------------------
  c0 <- control + 1L
  others <- setdiff(seq_len(k), c0)
  d_diff <- col_means[others] - col_means[c0]
  d_se <- rep(sqrt(2 * ms_e / n), length(others))
  if (defined) {
    d_t <- d_diff / d_se
    lambda <- rep(sqrt(0.5), length(others))
    d_p <- vapply(abs(d_t), dunnett_upper, numeric(1), lambda = lambda, df = df_e)
    crit <- critical_value(function(x) dunnett_upper(x, lambda, df_e))
    d_lwr <- d_diff - crit * d_se
    d_upr <- d_diff + crit * d_se
    long2 <- long
    long2$treatment <- relevel(long2$treatment, ref = as.character(c0))
    fit2 <- lm(y ~ treatment + subject, data = long2)
    set.seed(1L)
    gl <- glht(fit2, linfct = mcp(treatment = "Dunnett"))
    sm <- summary(gl, test = adjusted("single-step"))
    order_mc <- match(as.character(others), levels(long2$treatment)[-1])
    stopifnot(isTRUE(all.equal(unname(sm$test$coefficients)[order_mc], d_diff,
                               tolerance = 1e-10, scale = max(abs(col_means)))),
              isTRUE(all.equal(unname(sm$test$sigma)[order_mc], d_se, tolerance = 1e-8)))
    err <- attr(sm$test$pvalues, "error")
    mc_p <- unname(as.numeric(sm$test$pvalues))[order_mc]
    stopifnot(all(abs(mc_p - d_p) <= 3 * err + 1e-7 * d_p + 1e-12))
    message(sprintf("  rm_dunnett/%s: multcomp P within %.1e (its error %.1e)",
                    case, max(abs(mc_p - d_p)), err))
  } else {
    d_t <- d_p <- d_lwr <- d_upr <- rep(NaN, length(d_diff))
  }
  write_fixture(
    "rm_dunnett", case, description, inputs,
    expected = list(group = as.integer(others - 1L), difference = d_diff, se = d_se,
                    t = d_t, p_value = d_p, ci_lower = d_lwr, ci_upper = d_upr),
    kinds = c(group = "exact", difference = "statistic", se = "statistic",
              t = "statistic", p_value = "p_value", ci_lower = "ci", ci_upper = "ci"),
    packages = c("stats", "multcomp", "mvtnorm")
  )

  # --- Holm ----------------------------------------------------------------------
  if (defined) {
    h_t <- diff / se
    raw <- 2 * pt(abs(h_t), df_e, lower.tail = FALSE)
    adj <- p.adjust(raw, method = "holm")
    em <- summary(pairs(emmeans(fit, "treatment"), adjust = "holm", reverse = TRUE))
    em_raw <- summary(pairs(emmeans(fit, "treatment"), adjust = "none", reverse = TRUE))
    lab <- paste0("treatment", pj, " - treatment", pi)
    m <- match(lab, as.character(em$contrast))
    stopifnot(isTRUE(all.equal(em_raw$p.value[m], raw, tolerance = 1e-8)),
              isTRUE(all.equal(em$p.value[m], adj, tolerance = 1e-8)))
  } else {
    h_t <- raw <- adj <- rep(NaN, length(diff))
  }
  write_fixture(
    "rm_holm", case, description, inputs,
    expected = list(group_i = as.integer(pi - 1L), group_j = as.integer(pj - 1L),
                    difference = diff, se = se, t = h_t,
                    df = scalar(as.integer(df_e)),
                    p_unadjusted = raw, p_value = adj),
    kinds = c(group_i = "exact", group_j = "exact", difference = "statistic",
              se = "statistic", t = "statistic", df = "exact",
              p_unadjusted = "p_value", p_value = "p_value"),
    packages = c("stats", "emmeans")
  )
}

# Subject effects plus treatment effects plus noise.
simulate <- function(n, effects, subject_sd = 2, noise_sd = 1, noise_sds = NULL) {
  subject <- rnorm(n, 0, subject_sd)
  sds <- if (is.null(noise_sds)) rep(noise_sd, length(effects)) else noise_sds
  lapply(seq_along(effects), function(j) subject + effects[j] + rnorm(n, 0, sds[j]))
}

# --- Cases -------------------------------------------------------------------------

rm_case("basic", "Eight subjects, four treatments.",
        simulate(8, c(10, 11, 12.5, 10.5)))

rm_case("n_two", "Two subjects, three treatments (df_error = 2).",
        list(c(1.2, 2.9), c(2.0, 3.1), c(3.4, 5.6)))

rm_case("ties", "Repeated values within and across treatments.",
        list(c(3, 3, 4, 4, 5, 5), c(4, 4, 4, 5, 5, 6), c(5, 5, 6, 6, 6, 7)))

rm_case("missing", "Two incomplete subjects are left out; an empty row is ignored.",
        list(c(5.2, 6.0, NaN, 5.8, 4.9, 6.3, 5.5, NaN),
             c(6.1, 7.0, 6.4, 7.7, NaN, 7.2, 6.6, NaN),
             c(5.0, 5.9, 5.1, 6.4, 5.3, 6.8, 5.7, NaN)))

rm_case("missing_many", "Most rows have a missing value: 3 complete subjects of 10.",
        list(c(1.0, NaN, 2.2, 3.1, NaN, 2.6, NaN, 1.9, NaN, 2.4),
             c(1.8, 2.1, NaN, 3.9, 2.8, 3.0, 3.0, 2.7, NaN, NaN),
             c(2.5, 2.9, 3.3, 3.7, 3.6, 3.4, NaN, NaN, 3.0, 3.1),
             c(2.2, 2.0, 2.9, 4.4, NaN, 3.1, 3.2, NaN, 2.8, 3.0)))

rm_case("additive", "Exactly additive data (subject + treatment): no residual variation.",
        list(c(1, 2, 3, 5), c(2, 3, 4, 6), c(4, 5, 6, 8)))

rm_case("constant", "Every value is the same.",
        list(c(2, 2, 2, 2), c(2, 2, 2, 2), c(2, 2, 2, 2)))

rm_case("no_treatment_effect", "Each subject's values are a permutation of the same three.",
        list(c(1, 2, 3, 1, 2, 3), c(2, 3, 1, 3, 1, 2), c(3, 1, 2, 2, 3, 1)))

rm_case("sphericity_violated",
        "Very different spread per treatment: Greenhouse-Geisser epsilon well below 1.",
        simulate(12, c(0, 0.5, 1, 1.5, 2), subject_sd = 1,
                 noise_sds = c(0.2, 0.2, 0.5, 2, 6)))

rm_case("two_treatments", "Two treatments: F = t^2 of the paired t test, epsilon = 1.",
        simulate(9, c(0, 0.8)))

rm_case("many_treatments", "Eight treatments, ten subjects (seven Dunnett comparisons).",
        simulate(10, c(0, 0.2, 0.9, 0.4, 1.5, 0.1, 0.6, 1.1)))

rm_case("control_not_first", "Dunnett's control is the third treatment.",
        simulate(7, c(20, 22, 19, 25)), control = 2L)

rm_case("tiny_p", "Large treatment effects relative to the noise: very small P values.",
        simulate(15, c(0, 3, 6), noise_sd = 0.5))

rm_case("large_offset", "Small spread on a large offset.",
        lapply(simulate(6, c(0, 0.3, 0.1), subject_sd = 0.2, noise_sd = 0.05),
               function(v) 1e6 + v))

rm_case("large_n", "Two hundred subjects, three treatments.",
        simulate(200, c(0, 0.1, 0.25)))
