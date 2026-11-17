# Two-way ANOVA with interaction (Type III sums of squares) and Tukey's test on the
# marginal means of each factor.
#
# Curvis: curvis.analysis.compare.two_way_anova.
#
# Each case writes three fixtures: two_way_anova, two_way_tukey_a and two_way_tukey_b.
# Curvis shows Tukey's test only for a factor whose main effect is significant
# (P < 0.05); the fixtures have both, so both are checked in every case.
#
# Definitions (Type III confirmed by the project owner for step 3.1):
# - Input: cells[[i]][[j]], the values of level i of factor A (the series) at level j
#   of factor B (the category groups); a levels of A, b levels of B. Missing values
#   (NaN) are left out. Every cell needs at least one value, and the residual df
#   N - a b must be positive. Level indices in the fixtures start at 0; cell outputs
#   are in A-major order (cell (i, j) at position i b + j).
# - The model is y ~ A + B + A:B. Cell means mu (A-major) with counts n; D = diag(1/n).
#   Each effect is tested with the hypothesis L mu = 0 on the cell means, with
#     A:   L = C_a (x) (1_b' / b),   B:   L = (1_a' / a) (x) C_b,   A:B:   L = C_a (x) C_b
#   (C_m = [I_(m-1), -1], any full-rank contrasts give the same result). The Type III
#   sum of squares is SS = (L mu)' (L D L')^-1 (L mu), on rank(L) df: a - 1, b - 1 and
#   (a - 1)(b - 1). It is exactly 0 when L mu = 0. SS_residual is the sum over cells of
#   the squared deviations from the cell mean, on N - a b df. F = MS_effect / MS_residual.
#   Checked against car::Anova(lm(y ~ A * B) with contr.sum, type = 3). With
#   SS_residual = 0 the F and P values are NaN.
# - Marginal means are unweighted: the mean of the cell means over the other factor
#   (emmeans' default), so each level of the other factor counts equally whatever its n.
# - Tukey for factor A (B likewise): pairs (i, j), i < j, difference = marginal mean j -
#   marginal mean i, SE = sqrt(MS_residual / b^2 * sum_l (1/n_il + 1/n_jl)),
#   q = |difference| / (SE / sqrt(2)), P = P(Q > q) for the studentized range of a
#   means on the residual df, 95% CI difference -/+ q_crit SE / sqrt(2) (Tukey-Kramer
#   for unequal SEs, as emmeans' adjust = "tukey"). P and q_crit are the exact
#   integrals in R/posthoc.R, checked against emmeans within ptukey's accuracy.

suppressPackageStartupMessages({
  library(car)
  library(emmeans)
})
source(file.path(r_project_dir(), "R", "posthoc.R"), local = TRUE)

contrasts_full <- function(m) cbind(diag(m - 1L), -1)[seq_len(m - 1L), , drop = FALSE]

two_way_case <- function(case, description, cells) {
  a <- length(cells)
  b <- unique(lengths(cells))
  stopifnot(a >= 2L, length(b) == 1L, b >= 2L)
  cells <- lapply(cells, function(row) lapply(row, as.double))
  kept <- lapply(cells, function(row) lapply(row, function(v) v[!is.na(v)]))
  flat <- unlist(kept, recursive = FALSE)  # A-major
  n <- lengths(flat)
  stopifnot(all(n >= 1L))
  N <- sum(n)
  df_r <- N - a * b
  stopifnot(df_r > 0L)
  mu <- vapply(flat, mean, numeric(1))
  ss_r <- sum(vapply(flat, function(v) sum((v - mean(v))^2), numeric(1)))
  ms_r <- ss_r / df_r
  defined <- ss_r > 0

  L_a <- kronecker(contrasts_full(a), matrix(1 / b, 1, b))
  L_b <- kronecker(matrix(1 / a, 1, a), contrasts_full(b))
  L_ab <- kronecker(contrasts_full(a), contrasts_full(b))
  D <- diag(1 / n, length(n))
  effect <- function(L) {
    est <- drop(L %*% mu)
    ss <- if (all(est == 0)) 0 else drop(t(est) %*% solve(L %*% D %*% t(L), est))
    df <- nrow(L)
    ms <- ss / df
    f <- if (defined) ms / ms_r else NaN
    p <- if (defined) pf(f, df, df_r, lower.tail = FALSE) else NaN
    list(ss = ss, df = df, ms = ms, f = f, p = p)
  }
  ea <- effect(L_a)
  eb <- effect(L_b)
  eab <- effect(L_ab)

  # Cross-check with car::Anova, Type III with sum-to-zero contrasts.
  long <- data.frame(
    y = unlist(flat),
    A = factor(rep(rep(seq_len(a), each = b), n), levels = seq_len(a)),
    B = factor(rep(rep(seq_len(b), times = a), n), levels = seq_len(b))
  )
  fit <- lm(y ~ A * B, data = long, contrasts = list(A = "contr.sum", B = "contr.sum"))
  # car refuses a fit with no residual variation (constant cells); the SS then come
  # from the same formula as in every other case, and F and P are NaN.
  tab <- if (defined) car::Anova(fit, type = 3) else NULL
  # Allowed difference: relative to the total SS, with a floor at rounding level of the
  # data (the QR fit leaves ~1e-30 where the SS is exactly 0, e.g. identical cells).
  ss_scale <- sum((long$y - mean(long$y))^2)
  ss_tol <- 1e-8 * ss_scale + (64 * .Machine$double.eps * max(abs(long$y)))^2 * N
  check_ss <- function(row, e) {
    if (is.null(tab)) return(invisible())
    stopifnot(tab[row, "Df"] == e$df,
              abs(tab[row, "Sum Sq"] - e$ss) <= ss_tol)
    if (defined && e$ss > 1e-6 * ss_scale) {
      stopifnot(isTRUE(all.equal(tab[row, "F value"], e$f, tolerance = 1e-8)),
                isTRUE(all.equal(tab[row, "Pr(>F)"], e$p, tolerance = 1e-8)))
    }
  }
  check_ss("A", ea)
  check_ss("B", eb)
  check_ss("A:B", eab)
  stopifnot(is.null(tab) || tab["Residuals", "Df"] == df_r,
            is.null(tab) || abs(tab["Residuals", "Sum Sq"] - ss_r) <= ss_tol)

  M <- matrix(mu, a, b, byrow = TRUE)  # M[i, j] = cell (i, j)
  Ninv <- matrix(1 / n, a, b, byrow = TRUE)
  marg_a <- rowMeans(M)
  marg_b <- colMeans(M)

  inputs <- list(cells = cells)
  write_fixture(
    "two_way_anova", case, description, inputs,
    expected = list(n = as.integer(n), cell_mean = mu,
                    marginal_mean_a = marg_a, marginal_mean_b = marg_b,
                    df_a = scalar(as.integer(ea$df)), df_b = scalar(as.integer(eb$df)),
                    df_interaction = scalar(as.integer(eab$df)),
                    df_residual = scalar(as.integer(df_r)),
                    ss_a = scalar(ea$ss), ss_b = scalar(eb$ss),
                    ss_interaction = scalar(eab$ss), ss_residual = scalar(ss_r),
                    ms_a = scalar(ea$ms), ms_b = scalar(eb$ms),
                    ms_interaction = scalar(eab$ms), ms_residual = scalar(ms_r),
                    f_a = scalar(ea$f), f_b = scalar(eb$f),
                    f_interaction = scalar(eab$f),
                    p_a = scalar(ea$p), p_b = scalar(eb$p),
                    p_interaction = scalar(eab$p)),
    kinds = c(n = "exact", cell_mean = "statistic", marginal_mean_a = "statistic",
              marginal_mean_b = "statistic", df_a = "exact", df_b = "exact",
              df_interaction = "exact", df_residual = "exact", ss_a = "statistic",
              ss_b = "statistic", ss_interaction = "statistic",
              ss_residual = "statistic", ms_a = "statistic", ms_b = "statistic",
              ms_interaction = "statistic", ms_residual = "statistic",
              f_a = "statistic", f_b = "statistic", f_interaction = "statistic",
              p_a = "p_value", p_b = "p_value", p_interaction = "p_value"),
    packages = c("stats", "car")
  )

  # --- Tukey on the marginal means of one factor -----------------------------------
  # inv_n: levels x other-levels matrix of 1/n; marg: the marginal means.
  tukey_factor <- function(fixture, factor_name, marg, inv_n) {
    m <- length(marg)
    other <- ncol(inv_n)
    pairs <- do.call(rbind, lapply(seq_len(m - 1L), function(i) cbind(i, (i + 1L):m)))
    pi <- pairs[, 1]
    pj <- pairs[, 2]
    diff <- marg[pj] - marg[pi]
    se <- sqrt(ms_r / other^2 * (rowSums(inv_n[pi, , drop = FALSE]) +
                                 rowSums(inv_n[pj, , drop = FALSE])))
    if (defined) {
      q <- abs(diff) / (se / sqrt(2))
      p <- vapply(q, tukey_upper, numeric(1), k = m, df = df_r)
      q_crit <- critical_value(function(x) tukey_upper(x, m, df_r))
      lwr <- diff - q_crit * se / sqrt(2)
      upr <- diff + q_crit * se / sqrt(2)
      em <- suppressMessages(summary(
        pairs(emmeans(fit, factor_name), adjust = "tukey", reverse = TRUE),
        infer = c(TRUE, TRUE)))
      lab <- paste0(factor_name, pj, " - ", factor_name, pi)
      idx <- match(lab, as.character(em$contrast))
      stopifnot(!anyNA(idx),
                isTRUE(all.equal(em$estimate[idx], diff, tolerance = 1e-10,
                                 scale = max(abs(mu)))),
                isTRUE(all.equal(em$SE[idx], se, tolerance = 1e-8)))
      usable <- p >= 1e-6
      rel <- max(c(0, abs(em$p.value[idx][usable] - p[usable]) / p[usable]))
      message(sprintf("  %s/%s: emmeans (ptukey) P rel. diff %.1e", fixture, case, rel))
      stopifnot(rel < 1e-2)
    } else {
      q <- p <- lwr <- upr <- rep(NaN, length(diff))
    }
    write_fixture(
      fixture, case, description, inputs,
      expected = list(group_i = as.integer(pi - 1L), group_j = as.integer(pj - 1L),
                      difference = diff, se = se, q = q, p_value = p,
                      ci_lower = lwr, ci_upper = upr),
      kinds = c(group_i = "exact", group_j = "exact", difference = "statistic",
                se = "statistic", q = "statistic", p_value = "p_value",
                ci_lower = "ci", ci_upper = "ci"),
      packages = c("stats", "emmeans")
    )
  }
  tukey_factor("two_way_tukey_a", "A", marg_a, Ninv)
  tukey_factor("two_way_tukey_b", "B", marg_b, t(Ninv))
}

# cells[[i]][[j]] ~ normal with the given cell means (a x b matrix) and sizes.
simulate <- function(means, sizes, sd = 1) {
  sizes <- matrix(sizes, nrow(means), ncol(means))
  lapply(seq_len(nrow(means)), function(i)
    lapply(seq_len(ncol(means)), function(j) rnorm(sizes[i, j], means[i, j], sd)))
}

# --- Cases -------------------------------------------------------------------------

two_way_case("basic", "2 x 3 design, 4 values per cell, both main effects.",
             simulate(rbind(c(10, 12, 13), c(11, 13, 15)), 4L))

two_way_case("interaction", "2 x 2 crossover interaction, 6 values per cell.",
             simulate(rbind(c(5, 8), c(8, 5)), 6L))

two_way_case("no_effects", "3 x 2 design with equal true means.",
             simulate(matrix(4, 3, 2), 5L))

two_way_case("unbalanced", "3 x 2 design with cell sizes 2 to 9.",
             simulate(rbind(c(20, 23), c(21, 27), c(19, 24)),
                      rbind(c(3L, 9L), c(5L, 2L), c(7L, 4L)), sd = 2))

two_way_case("missing", "Missing values in several cells.",
             list(list(c(5.1, NaN, 6.2, 5.8), c(7.0, 7.4, NaN, 6.9)),
                  list(c(NaN, NaN, 4.8, 5.5), c(8.1, 8.8, 9.0, 8.2)),
                  list(c(6.6, 6.0, 5.7, NaN), c(NaN, 7.9, 8.4, 7.7))))

two_way_case("n_two", "2 x 3 design with 2 values per cell (df_residual = 6).",
             list(list(c(1.2, 1.9), c(2.4, 3.0), c(3.1, 2.2)),
                  list(c(2.0, 2.8), c(3.9, 3.3), c(4.6, 5.1))))

two_way_case("single_values", "Some cells hold a single value.",
             list(list(c(3.2), c(4.1, 4.6, 3.9)),
                  list(c(5.0, 5.7, 4.4), c(6.3)),
                  list(c(4.0, 4.8), c(7.2, 6.6, 7.9))))

two_way_case("ties", "Repeated values within and across cells.",
             list(list(c(3, 3, 4, 4), c(4, 4, 5, 5)),
                  list(c(4, 4, 4, 5), c(6, 6, 7, 7))))

two_way_case("identical", "Every cell has the same values: all F = 0, P = 1.",
             rep(list(rep(list(c(2.5, 3.1, 4.7)), 3)), 2))

two_way_case("constant_cells", "Every cell is constant (different values): no residual variation.",
             list(list(c(1, 1, 1), c(2, 2, 2)), list(c(3, 3, 3), c(5, 5, 5))))

two_way_case("many_levels", "4 x 3 design, unbalanced.",
             simulate(outer(c(0, 0.5, 1.5, 1), c(0, 1, 0.3), "+"),
                      rbind(c(4L, 5L, 6L), c(6L, 4L, 5L), c(5L, 6L, 4L), c(4L, 4L, 7L))))

two_way_case("tiny_p", "Large effects, 10 values per cell: very small P values.",
             simulate(rbind(c(0, 4), c(3, 9)), 10L, sd = 0.5))

two_way_case("large_offset", "Small spread on a large offset.",
             lapply(simulate(rbind(c(0, 0.3), c(0.2, 0.4)), 4L, sd = 0.05),
                    function(row) lapply(row, function(v) 1e6 + v)))
