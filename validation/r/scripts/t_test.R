# t tests: unpaired (Student, or Welch's correction for unequal variances) and paired,
# with the mean difference and its 95% confidence interval.
#
# Curvis: curvis.analysis.compare.ttest.
#
# Definitions (stats::t.test, two-sided, 95% confidence):
# - Groups A (the first) and B (the second). The difference is B - A, so the fixture
#   calls t.test(b, a, ...). Missing values (NaN) are left out.
# - Unpaired, Student: pooled variance, df = n_a + n_b - 2 (t.test(var.equal = TRUE)).
# - Unpaired, Welch: separate variances, Welch-Satterthwaite df (t.test(var.equal = FALSE)).
# - Paired: values are paired by row; a pair with a missing value is left out (as
#   t.test does). The test is the one-sample t test of the differences B - A.
# - Outputs: n per group (or the number of pairs), group means (of the values used), the
#   difference, its standard error, t, df, the two-sided P value and the 95% CI.
#
# t.test refuses some inputs, and the expected values are then stated here:
# - Too few values (unpaired: n_a + n_b < 3, or n < 2 in a group with Welch's correction;
#   paired: fewer than 2 pairs): SE, t, df, P and CI are NaN; n, means and difference are
#   kept when they exist.
# - No variation (the SE is 0, or tiny next to the means: "data are essentially
#   constant"): SE, t, P and CI are NaN, df is kept. t would be 0/0 or infinite, so it is
#   not reported. t.test's own test is repeated here (its error message is translated).

t_test_fixture <- function(case, description, a, b, variant) {
  stopifnot(variant %in% c("student", "welch", "paired"))
  a <- as.double(a)
  b <- as.double(b)
  paired <- variant == "paired"
  if (paired) {
    stopifnot(length(a) == length(b))
    keep <- !is.na(a) & !is.na(b)
    x <- b[keep]
    y <- a[keep]
  } else {
    x <- b[!is.na(b)]
    y <- a[!is.na(a)]
  }
  nx <- length(x)
  ny <- length(y)
  mean_or_nan <- function(v) if (length(v) > 0L) mean(v) else NaN
  out <- list(
    mean_a = mean_or_nan(y), mean_b = mean_or_nan(x),
    difference = if (paired && nx > 0L) mean(x - y) else mean_or_nan(x) - mean_or_nan(y),
    se = NaN, t = NaN, df = NaN, p_value = NaN, ci_lower = NaN, ci_upper = NaN
  )

  too_few <- if (paired) nx < 2L
             else if (variant == "welch") nx < 2L || ny < 2L
             else nx < 1L || ny < 1L || nx + ny < 3L
  if (!too_few) {
    # t.test's checks, repeated so the stated rule is used instead of an error.
    if (paired) {
      d <- x - y
      df <- nx - 1
      se <- sqrt(var(d) / nx)
      scale <- abs(mean(d))
    } else if (variant == "welch") {
      vx <- var(x) / nx
      vy <- var(y) / ny
      se <- sqrt(vx + vy)
      df <- se^4 / (vx^2 / (nx - 1) + vy^2 / (ny - 1))
      scale <- max(abs(mean(x)), abs(mean(y)))
    } else {
      df <- nx + ny - 2
      v <- 0
      if (nx > 1L) v <- v + (nx - 1) * var(x)
      if (ny > 1L) v <- v + (ny - 1) * var(y)
      v <- v / df
      se <- sqrt(v * (1 / nx + 1 / ny))
      scale <- max(abs(mean(x)), abs(mean(y)))
    }
    if (variant != "welch" || is.finite(df)) out$df <- df
    if (is.finite(se) && se > 0 && se >= 10 * .Machine$double.eps * scale) {
      tt <- t.test(x, y, paired = paired, var.equal = variant == "student",
                   conf.level = 0.95)
      out$se <- unname(tt$stderr)
      out$t <- unname(tt$statistic)
      out$df <- unname(tt$parameter)
      out$p_value <- tt$p.value
      out$ci_lower <- tt$conf.int[1]
      out$ci_upper <- tt$conf.int[2]
      stopifnot(isTRUE(all.equal(unname(tt$estimate[1] - if (paired) 0 else tt$estimate[2]),
                                 out$difference, tolerance = 1e-13)))
    }
  }

  counts <- if (paired) list(n_pairs = as.integer(nx))
            else list(n_a = as.integer(ny), n_b = as.integer(nx))
  expected <- c(counts, out)
  kinds <- c(
    if (paired) c(n_pairs = "exact") else c(n_a = "exact", n_b = "exact"),
    mean_a = "statistic", mean_b = "statistic", difference = "statistic",
    se = "statistic", t = "statistic",
    df = if (variant == "welch") "statistic" else "exact",
    p_value = "p_value", ci_lower = "ci", ci_upper = "ci"
  )
  write_fixture(
    analysis = "t_test",
    case = paste0(case, "_", variant),
    description = paste0(description, " (", switch(variant,
      student = "unpaired, Student", welch = "unpaired, Welch's correction",
      paired = "paired"), ")"),
    inputs = list(a = a, b = b, paired = scalar(paired), welch = scalar(variant == "welch")),
    expected = expected,
    kinds = kinds,
    packages = "stats"
  )
}

# Writes the unpaired fixtures, and the paired one when the groups have equal length.
t_test_cases <- function(case, description, a, b, paired = length(a) == length(b)) {
  t_test_fixture(case, description, a, b, "student")
  t_test_fixture(case, description, a, b, "welch")
  if (paired) t_test_fixture(case, description, a, b, "paired")
}

# --- Cases -------------------------------------------------------------------------

t_test_cases("basic", "Ten normal values per group, different means and SDs.",
             rnorm(10, mean = 5, sd = 1), rnorm(10, mean = 6.2, sd = 1.8))

t_test_cases("n_small", "n = 2 per group.", c(1.2, 2.9), c(3.1, 4.6))

t_test_cases("ties", "Repeated values within and across groups.",
             c(2, 2, 3, 3, 3, 4, 5, 5), c(3, 3, 4, 4, 4, 5, 6, 6))

t_test_cases("missing", "Missing values in both groups; paired drops incomplete pairs.",
             c(4.1, NaN, 5.3, 6.0, 4.8, NaN, 5.5, 5.9, 4.4, 5.0),
             c(5.0, 5.8, NaN, 6.9, 5.1, 6.4, NaN, 6.2, 5.6, 5.8))

t_test_cases("unequal_sizes", "3 against 25 values with different SDs: Student and Welch differ.",
             c(10.2, 12.9, 8.4), rnorm(25, mean = 12, sd = 4))

t_test_cases("identical", "Both groups hold the same values: the difference is 0, P = 1.",
             c(3.2, 4.8, 5.1, 6.3, 2.9), c(3.2, 4.8, 5.1, 6.3, 2.9),
             paired = FALSE)

t_test_fixture("identical", "Both groups hold the same values: every difference is 0, so the paired t is not defined",
               c(3.2, 4.8, 5.1, 6.3, 2.9), c(3.2, 4.8, 5.1, 6.3, 2.9), "paired")

t_test_cases("constant_shift", "B = A + 1 exactly: the paired differences have no variation.",
             c(1.5, 2.5, 4, 7, 11), c(2.5, 3.5, 5, 8, 12))

t_test_cases("zero_variance_one", "Group A is constant, group B is not.",
             c(5, 5, 5, 5, 5, 5), c(4.2, 5.9, 6.1, 5.3, 7.0, 6.6))

t_test_cases("constant_both", "Each group is constant (different values): no variation at all.",
             c(2, 2, 2, 2), c(3, 3, 3, 3))

t_test_cases("n_one", "One value in group A: Student's test still has df = n_b - 1; Welch's needs 2.",
             c(7.5), c(5.1, 6.2, 5.8, 6.9, 6.0), paired = FALSE)

t_test_cases("tiny_p", "40 values per group with a large effect: a very small P value.",
             rnorm(40, mean = 0, sd = 1), rnorm(40, mean = 4, sd = 1))

t_test_cases("large_offset", "Small spread on a large offset.",
             1e9 + c(0.11, 0.25, 0.32, 0.18, 0.29), 1e9 + c(0.41, 0.36, 0.52, 0.47, 0.39))

t_test_cases("unequal_paired", "Paired data with spread differences.",
             c(120, 135, 128, 141, 117, 133, 125, 139, 130, 122, 136, 127),
             c(115, 131, 129, 132, 116, 125, 124, 130, 126, 121, 128, 120))
