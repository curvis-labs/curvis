# Descriptive statistics of replicate groups: for each row of a table of replicate
# columns, n, mean, SD, SEM and the 95% confidence interval of the mean.
#
# Curvis: curvis.data.replicates.row_stats (n, mean, SD, SEM) and mean_ci (95% CI).
#
# Definitions (the same as GraphPad Prism and R):
# - Missing values (NaN) are left out; n counts the rest. Infinite values are not missing.
# - mean: base::mean. NaN when n = 0.
# - SD: stats::sd, the sample standard deviation (n - 1 in the denominator). NaN when n < 2.
# - SEM: SD / sqrt(n). NaN when n < 2.
# - 95% CI of the mean: mean -/+ qt(0.975, n - 1) * SEM, taken from stats::t.test.
#   t.test refuses some inputs, and the expected value is then stated here:
#   - n < 2, or SD not finite (infinite values): NaN.
#   - SD = 0 (constant data): the CI is [mean, mean].
#   - SD > 0 but tiny next to the mean (t.test: "data are essentially constant", e.g.
#     1e15 + small spread): the same formula with stats::qt, which is what t.test
#     computes when it does accept the data.

describe_row <- function(row) {
  x <- row[!is.na(row)]  # drops NA and NaN, keeps -Inf and Inf
  n <- length(x)
  m <- if (n > 0L) mean(x) else NaN
  s <- if (n >= 2L) sd(x) else NaN
  sem <- s / sqrt(n)
  ci <- ci95_of_mean(x, m, sem)
  list(n = n, mean = m, sd = s, sem = sem, ci_lower = ci[1], ci_upper = ci[2])
}

ci95_of_mean <- function(x, m, sem) {
  n <- length(x)
  if (n < 2L || !is.finite(sem)) return(c(NaN, NaN))
  if (sem == 0) return(c(m, m))
  formula <- m + c(-1, 1) * qt(0.975, df = n - 1) * sem
  # t.test's own test for refusing the data (its error message is translated, so the
  # rule is repeated here instead of matching the message).
  if (sqrt(var(x) / n) < 10 * .Machine$double.eps * abs(m)) return(formula)
  tested <- as.numeric(t.test(x, conf.level = 0.95)$conf.int)
  # t.test and the formula differ at most by rounding (sqrt(var / n) vs sd / sqrt(n)).
  stopifnot(isTRUE(all.equal(tested, formula, tolerance = 1e-13)))
  tested
}

# rows: list of numeric vectors, one per table row, all as long as the number of
# replicate columns. Writes the replicate columns as inputs and the per-row statistics
# as expected outputs.
descriptive_fixture <- function(case, description, rows) {
  widths <- lengths(rows)
  stopifnot(length(rows) > 0L, all(widths == widths[1]))
  table <- do.call(rbind, rows)  # rows x replicates
  storage.mode(table) <- "double"
  stats <- lapply(rows, describe_row)
  column <- function(name) vapply(stats, `[[`, numeric(1), name)
  write_fixture(
    analysis = "descriptive_stats",
    case = case,
    description = description,
    inputs = list(replicates = table),  # written as a list of replicate columns
    expected = list(
      n = as.integer(column("n")),
      mean = column("mean"),
      sd = column("sd"),
      sem = column("sem"),
      ci_lower = column("ci_lower"),
      ci_upper = column("ci_upper")
    ),
    kinds = c(n = "exact", mean = "statistic", sd = "statistic", sem = "statistic",
              ci_lower = "ci", ci_upper = "ci"),
    packages = "stats"
  )
}

# Splits a vector into rows of `width` values.
as_rows <- function(values, width) {
  split(values, rep(seq_len(length(values) / width), each = width))
}

# --- Cases -------------------------------------------------------------------------

descriptive_fixture(
  "basic", "Ten rows of three normal replicates with different means and SDs.",
  as_rows(rnorm(30, mean = rep(c(1, 10, 100, -5, 0.01, 1e3, 2.5, 50, -0.3, 7), each = 3),
                sd = rep(c(0.1, 1, 15, 2, 0.002, 80, 0.5, 5, 0.05, 3), each = 3)), 3)
)

descriptive_fixture(
  "n_small", "n = 0, 1 and 2 in three replicate columns (the others missing).",
  list(
    c(NaN, NaN, NaN),
    c(4.2, NaN, NaN),
    c(NaN, NaN, -3.5),
    c(5, 7, NaN),
    c(-1.5, NaN, 2.25),
    c(NaN, 0.1, 0.2),
    c(1e-8, 3e-8, NaN)
  )
)

descriptive_fixture(
  "single_column", "One replicate column, so n is 0 or 1 in every row.",
  as_rows(c(1.5, NaN, -2, 0, 1e6, NaN), 1)
)

descriptive_fixture(
  "missing", "Six replicates with missing values scattered so n differs per row (0 to 6).",
  local({
    values <- rnorm(72, mean = 20, sd = 4)
    values[sample(72, 30)] <- NaN
    values[1:6] <- NaN          # a row with n = 0
    values[7:11] <- NaN         # a row with n = 1
    values[67:72] <- rnorm(6)   # a row with n = 6
    as_rows(values, 6)
  })
)

descriptive_fixture(
  "ties", "Repeated values within rows.",
  list(
    c(2, 2, 3, 3),
    c(1, 1, 1, 5),
    c(0.1, 0.1, 0.2, 0.2),
    c(-3, -3, -3, 7),
    c(9.75, 9.75, NaN, 9.5),
    c(4, 4, 4, 4)
  )
)

descriptive_fixture(
  "constant", "Equal values in every row: SD and SEM are 0 and the CI is [mean, mean].",
  c(
    lapply(c(0, 0.1, 3.3, -7.77, 123456.789, 1e-300, 5e-324, 1e300, -1e300),
           function(v) rep(v, 5)),
    list(c(2.5, NaN, 2.5, 2.5, NaN))
  )
)

descriptive_fixture(
  "large_offset", "Small spread on a large offset, where naive sums lose precision.",
  list(
    1e9 + c(0.1, 0.2, 0.3, 0.4),
    -1e9 + c(0.1, 0.2, 0.3, 0.4),
    1e12 + rnorm(4),
    1e15 + c(1, 2, 3, 4),
    1e15 + c(0.5, 1.5, 2, 4.5),
    1e8 + c(1e-4, 2e-4, 3e-4, NaN),
    1e-12 * c(1, 2, 3, 4),
    1e-300 * c(1.5, 2.5, 3.5, 4.5)
  )
)

descriptive_fixture(
  "infinite", "Infinite values: the mean is infinite (or NaN), SD, SEM and CI are NaN.",
  list(
    c(1, 2, Inf),
    c(1, -Inf, 2),
    c(Inf, -Inf, 1),
    c(Inf, Inf, Inf),
    c(-Inf, -Inf, NaN),
    c(Inf, NaN, NaN),
    c(-Inf, NaN, 3)
  )
)

descriptive_fixture(
  "many_replicates", "100 replicates per row, some missing.",
  local({
    values <- rnorm(500, mean = rep(c(0, 5, -100, 1e4, 0.5), each = 100),
                    sd = rep(c(1, 0.5, 30, 250, 0.01), each = 100))
    values[sample(500, 40)] <- NaN
    as_rows(values, 100)
  })
)

# No case for values near the largest double (about 1e308): R's own results are wrong
# there and depend on the platform. sd(c(1.5e308, 1.7e308, 1.6e308)) is Inf (true SD
# 1e307), and mean(c(1e308, -1e308, 5)) is 2.78 (true mean 5/3), because the
# refinement pass subtracts the mean in double precision. A fixture must hold correct
# answers, so this range is left untested.
