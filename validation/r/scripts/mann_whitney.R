# Mann-Whitney U test (Wilcoxon rank sum test) for two groups, two-sided, with the exact
# or the normal-approximation method chosen the way stats::wilcox.test chooses it.
#
# Curvis: curvis.analysis.compare.mann_whitney.
#
# Definitions (stats::wilcox.test(a, b), defaults: exact = NULL, correct = TRUE):
# - Missing values (NaN) are left out. Values are ranked together; ties get midranks.
# - U = sum of the ranks of group A - n_a (n_a + 1) / 2: the number of pairs (a, b) with
#   a > b, ties counting 1/2 (wilcox.test's W for x = a).
# - Method (R >= 4.6): exact when n_a < 50 and n_b < 50, also with ties (then the exact
#   distribution of U given the tied ranks); otherwise the normal approximation with
#   continuity correction and tie correction of the variance.
#   Before R 4.6, ties forced the normal approximation, so this script needs R >= 4.6.
# - Exact two-sided P = min(1, 2 P(U <= u), 2 P(U >= u)).
# - Normal approximation: z = (u - n_a n_b / 2 - sign(.) / 2) / sigma; P = 2 P(Z > |z|).
#   With all values tied (sigma = 0) z is not defined and R's P is NaN.
# - Also the median of each group (stats::median).

if (getRversion() < "4.6.0" && !isTRUE(getOption("curvis.allow_old_r"))) {
  stop("mann_whitney.R needs R >= 4.6 (exact P values with ties)")
}

mann_whitney_fixture <- function(case, description, a, b) {
  a <- as.double(a)
  b <- as.double(b)
  x <- a[!is.na(a)]
  y <- b[!is.na(b)]
  wt <- suppressWarnings(wilcox.test(x, y, alternative = "two.sided"))
  exact <- grepl("exact", wt$method, fixed = TRUE)
  stopifnot(exact == (length(x) < 50L && length(y) < 50L) || getRversion() < "4.6.0")
  u <- unname(wt$statistic)
  ranks <- rank(c(x, y))
  stopifnot(u == sum(ranks[seq_along(x)]) - length(x) * (length(x) + 1) / 2)
  write_fixture(
    analysis = "mann_whitney",
    case = case,
    description = paste0(description, " (", wt$method, ")"),
    inputs = list(a = a, b = b),
    expected = list(
      n_a = scalar(length(x)), n_b = scalar(length(y)),
      u = scalar(u), p_value = scalar(wt$p.value),
      exact = scalar(as.integer(exact)),
      median_a = scalar(median(x)), median_b = scalar(median(y))
    ),
    kinds = c(n_a = "exact", n_b = "exact", u = "exact", p_value = "p_value",
              exact = "exact", median_a = "statistic", median_b = "statistic"),
    packages = "stats"
  )
}

# --- Cases -------------------------------------------------------------------------

mann_whitney_fixture("basic", "Eight against ten values, no ties.",
                     rnorm(8, 10, 2), rnorm(10, 12, 2))

mann_whitney_fixture("n_small", "n = 2 per group: the smallest P value is 1/3.",
                     c(1.5, 2.5), c(3.5, 4.5))

mann_whitney_fixture("ties", "Ties within and across groups, small n: exact with ties.",
                     c(1, 2, 2, 3, 3, 3, 5), c(2, 3, 4, 4, 5, 5, 6, 6))

mann_whitney_fixture("ties_half_ranks", "Ties that give half ranks.",
                     c(1.5, 1.5, 4, 7, 7), c(1.5, 3, 7, 9, 9, 10))

mann_whitney_fixture("missing", "Missing values in both groups.",
                     c(3.1, NaN, 4.4, 2.8, NaN, 5.0, 3.9),
                     c(4.9, 5.6, NaN, 6.1, 4.2, 5.3, NaN, 6.8))

mann_whitney_fixture("unequal_sizes", "3 against 30 values.",
                     c(9.5, 11.2, 14.0), rnorm(30, 12, 3))

mann_whitney_fixture("identical", "Both groups hold the same values: P = 1.",
                     c(2.2, 3.4, 5.1, 6.0), c(2.2, 3.4, 5.1, 6.0))

mann_whitney_fixture("all_tied", "Every value is the same.",
                     c(4, 4, 4, 4), c(4, 4, 4, 4, 4))

mann_whitney_fixture("separated", "Complete separation, 12 against 12: the smallest exact P.",
                     1:12 + 0.5, 13:24 + 0.5)

mann_whitney_fixture("boundary_exact", "49 against 49 values: still exact.",
                     rnorm(49, 0, 1), rnorm(49, 0.5, 1))

mann_whitney_fixture("boundary_normal", "49 against 50 values: normal approximation.",
                     rnorm(49, 0, 1), rnorm(50, 0.5, 1))

mann_whitney_fixture("large", "80 against 120 values: normal approximation.",
                     rnorm(80, 0, 1), rnorm(120, 0.3, 1.2))

mann_whitney_fixture("large_ties", "60 against 70 rounded values: normal approximation with ties.",
                     round(rnorm(60, 5, 2)), round(rnorm(70, 5.8, 2)))

mann_whitney_fixture("ties_exact_large", "45 against 40 rounded values: exact with many ties.",
                     round(rnorm(45, 5, 2)), round(rnorm(40, 6, 2)))
