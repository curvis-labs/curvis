# Kruskal-Wallis test for 3 or more groups, with Dunn's multiple comparisons test of all
# pairs or of each group against a control, P values adjusted with Holm's method.
#
# Curvis: curvis.analysis.compare.kruskal.
#
# Definitions:
# - Missing values (NaN) are left out. All values are ranked together; ties get midranks.
#   N is the total number of values, n_i and Rbar_i the size and mean rank of group i,
#   and C = sum(t^3 - t) over the groups of tied values (t values each).
# - Kruskal-Wallis (stats::kruskal.test): H, corrected for ties, is
#     H = (12 / (N (N + 1)) * sum(n_i Rbar_i^2) - 3 (N + 1)) / (1 - C / (N^3 - N)),
#   on df = k - 1, P = P(chi^2_df >= H). There is no exact P value (kruskal.test gives
#   none). With every value the same the tie correction is 0 / 0, so H and P are NaN.
# - Dunn's test (dunn.test::dunn.test, two-sided: altp = TRUE; its default is one-sided).
#   For groups i < j (in input order),
#     z = (Rbar_j - Rbar_i) / sqrt(S2 (1 / n_i + 1 / n_j)),
#     S2 = N (N + 1) / 12 - C / (12 (N - 1)),
#   and P = 2 P(Z >= |z|). The sign follows Curvis' "j - i" convention for pairs; the
#   script computes z by this formula and stops unless |z| and P agree with dunn.test to
#   1e-12. P values written are dunn.test's; z is the signed value of the formula.
# - Holm's adjustment: stats::p.adjust(method = "holm"), as for the ANOVA fixtures.
#   All pairs: over the k (k - 1) / 2 pairs. Against a control: over the k - 1
#   comparisons with the control only (as PMCMRplus::kwManyOneDunnTest), not by
#   adjusting all pairs and keeping the control's rows.
# - Also the median and the mean rank of each group.
#
# Inputs: groups (one array per group, NaN for missing) and control (0-based index of
# the control group). Each case writes kruskal_wallis/<case>, dunn_all_pairs/<case> and
# dunn_control/<case>.

kw_case <- function(case, description, groups, control = 0L) {
  groups <- lapply(groups, as.double)
  k <- length(groups)
  clean <- lapply(groups, function(g) g[!is.na(g)])
  n <- vapply(clean, length, integer(1))
  stopifnot(k >= 3L, all(n >= 1L), control >= 0L, control < k)

  values <- unlist(clean)
  g <- factor(rep(seq_len(k), n), levels = seq_len(k))
  big_n <- length(values)
  kt <- kruskal.test(values, g)
  h <- unname(kt$statistic)
  df <- unname(kt$parameter)
  p <- kt$p.value

  ranks <- rank(values)
  mean_rank <- as.vector(tapply(ranks, g, mean))
  medians <- vapply(clean, median, numeric(1))
  tie_sizes <- as.vector(table(values))
  ties_c <- sum(tie_sizes^3 - tie_sizes)

  # H by the formula above, as a check of the definition.
  h_formula <- (12 / (big_n * (big_n + 1)) * sum(n * mean_rank^2) - 3 * (big_n + 1)) /
    (1 - ties_c / (big_n^3 - big_n))
  stopifnot(is.nan(h) == is.nan(h_formula),
            is.nan(h) || isTRUE(all.equal(h, h_formula, tolerance = 1e-12)))

  # Dunn's z for every pair i < j, by the formula.
  s2 <- big_n * (big_n + 1) / 12 - ties_c / (12 * (big_n - 1))
  pairs <- t(combn(k, 2L))  # rows (i, j), i < j, in the order (1,2), (1,3), ..., (2,3), ...
  p_i <- pairs[, 1L]
  p_j <- pairs[, 2L]
  diff <- mean_rank[p_j] - mean_rank[p_i]
  z <- diff / sqrt(s2 * (1 / n[p_i] + 1 / n[p_j]))
  p_formula <- 2 * pnorm(-abs(z))

  p_raw <- dunn_p_values(clean, p_i, p_j, z, p_formula)

  inputs <- list(groups = groups, control = scalar(as.integer(control)))
  packages <- c("stats", "dunn.test")
  description <- paste0(description, " Kruskal-Wallis with Dunn's test, Holm-adjusted.")

  write_fixture(
    analysis = "kruskal_wallis", case = case, description = description,
    inputs = inputs,
    expected = list(n = as.integer(n), median = medians, mean_rank = mean_rank,
                    h = scalar(h), df = scalar(as.integer(df)), p_value = scalar(p)),
    kinds = c(n = "exact", median = "statistic", mean_rank = "statistic",
              h = "statistic", df = "exact", p_value = "p_value"),
    packages = packages
  )

  write_fixture(
    analysis = "dunn_all_pairs", case = case, description = description,
    inputs = inputs,
    expected = list(group_i = as.integer(p_i - 1L), group_j = as.integer(p_j - 1L),
                    mean_rank_diff = diff, z = z, p_unadjusted = p_raw,
                    p_value = p.adjust(p_raw, method = "holm")),
    kinds = c(group_i = "exact", group_j = "exact", mean_rank_diff = "statistic",
              z = "statistic", p_unadjusted = "p_value", p_value = "p_value"),
    packages = packages
  )

  # Against the control: the k - 1 pairs that contain it, in group order, as
  # group - control (so the sign of z flips where the control is the second group).
  c1 <- control + 1L
  others <- setdiff(seq_len(k), c1)
  row <- vapply(others, function(o) which((p_i == c1 & p_j == o) | (p_i == o & p_j == c1)),
                integer(1))
  flip <- ifelse(p_i[row] == c1, 1, -1)
  p_control <- p_raw[row]
  write_fixture(
    analysis = "dunn_control", case = case, description = description,
    inputs = inputs,
    expected = list(group = as.integer(others - 1L), mean_rank_diff = flip * diff[row],
                    z = flip * z[row], p_unadjusted = p_control,
                    p_value = p.adjust(p_control, method = "holm")),
    kinds = c(group = "exact", mean_rank_diff = "statistic", z = "statistic",
              p_unadjusted = "p_value", p_value = "p_value"),
    packages = packages
  )
}

# Unadjusted two-sided P values of dunn.test for the pairs (p_i, p_j), checked against z
# and P from the formula. dunn.test labels a comparison "a - b" with the group numbers
# of the list it is given.
dunn_p_values <- function(clean, p_i, p_j, z, p_formula) {
  if (all(is.nan(z))) {
    # Every value is the same: z = 0 / 0 for every pair. dunn.test is not asked.
    return(rep(NaN, length(z)))
  }
  out <- NULL
  invisible(utils::capture.output(
    out <- dunn.test::dunn.test(clean, method = "none", altp = TRUE, kw = FALSE,
                                table = FALSE, list = FALSE)
  ))
  p_field <- if (!is.null(out$altP)) out$altP else out$P
  labels <- strsplit(out$comparisons, " - ", fixed = TRUE)
  a <- as.integer(vapply(labels, `[`, character(1), 1L))
  b <- as.integer(vapply(labels, `[`, character(1), 2L))
  if (anyNA(a) || anyNA(b)) stop("unexpected dunn.test labels: ", toString(out$comparisons))
  match_row <- vapply(seq_along(p_i), function(r) {
    which((a == p_i[r] & b == p_j[r]) | (a == p_j[r] & b == p_i[r]))
  }, integer(1))
  z_pkg <- out$Z[match_row]
  p_pkg <- p_field[match_row]
  stopifnot(
    isTRUE(all.equal(abs(z_pkg), abs(z), tolerance = 1e-12)),
    isTRUE(all.equal(p_pkg, p_formula, tolerance = 1e-12))
  )
  p_pkg
}

# --- Cases -------------------------------------------------------------------------

kw_case("basic", "Three groups of 8, 10 and 9 values, no ties.",
        list(rnorm(8, 10, 2), rnorm(10, 12, 2), rnorm(9, 11, 2)))

kw_case("ties", "Rounded values: ties within and across groups.",
        list(c(1, 2, 2, 3, 3, 3, 5), c(2, 3, 4, 4, 5, 5, 6, 6), c(4, 5, 5, 6, 7, 7)))

kw_case("ties_heavy", "Four groups of scores 1 to 5: few distinct values, many ties.",
        list(sample(1:5, 15, replace = TRUE, prob = c(0.3, 0.3, 0.2, 0.1, 0.1)),
             sample(1:5, 12, replace = TRUE, prob = c(0.2, 0.2, 0.2, 0.2, 0.2)),
             sample(1:5, 18, replace = TRUE, prob = c(0.1, 0.1, 0.2, 0.3, 0.3)),
             sample(1:5, 10, replace = TRUE, prob = c(0.1, 0.2, 0.4, 0.2, 0.1))))

kw_case("unbalanced", "Four groups of 3, 12, 25 and 6 values.",
        list(rnorm(3, 5, 1), rnorm(12, 6, 1.5), rnorm(25, 5.5, 2), rnorm(6, 7, 1)))

kw_case("n_two", "n = 2 per group.",
        list(c(1.1, 2.3), c(3.4, 4.6), c(5.2, 6.8)))

kw_case("single_value_group", "One group has a single value.",
        list(4.2, rnorm(6, 5, 1), rnorm(7, 6, 1)))

kw_case("missing", "Missing values in every group.",
        list(c(3.1, NaN, 4.4, 2.8, NaN, 5.0, 3.9),
             c(4.9, 5.6, NaN, 6.1, 4.2, 5.3, NaN, 6.8),
             c(NaN, 7.2, 6.4, 8.1, 5.9, NaN)))

kw_case("control_not_first", "The control is the third of four groups.",
        list(rnorm(7, 12, 2), rnorm(8, 11, 2), rnorm(9, 9, 2), rnorm(6, 13, 2)),
        control = 2L)

kw_case("many_groups", "Six groups (15 pairs): Holm's step-down order matters.",
        lapply(c(0, 0.3, 0.8, 1.2, 2, 2.5), function(m) rnorm(9, m, 1)))

kw_case("identical", "Every group holds the same values: H = 0, P = 1.",
        list(c(2.2, 3.4, 5.1, 6.0), c(2.2, 3.4, 5.1, 6.0), c(2.2, 3.4, 5.1, 6.0)))

kw_case("constant_within", "Each group is constant, the groups differ.",
        list(c(2, 2, 2), c(5, 5, 5, 5), c(9, 9, 9)))

kw_case("constant_all", "Every value is the same: H, z and P are not defined (NaN).",
        list(c(4, 4, 4), c(4, 4, 4, 4), c(4, 4)))

kw_case("tiny_p", "Three separated groups of 40: P around 1e-23.",
        list(rnorm(40, 0, 1), rnorm(40, 5, 1), rnorm(40, 10, 1)))

kw_case("large", "Three groups of 200 rounded values: many ties.",
        list(round(rnorm(200, 10, 3)), round(rnorm(200, 10.5, 3)),
             round(rnorm(200, 11, 3))))
