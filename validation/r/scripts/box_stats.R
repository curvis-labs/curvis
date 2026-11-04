# Box-plot statistics: for each group of values, n, minimum, first quartile, median,
# third quartile and maximum.
#
# Curvis: curvis.charts.data.box_stats (the boxes and whiskers of box plots).
#
# Definitions (the same as GraphPad Prism's default box-and-whiskers plot):
# - Missing values (NaN) are left out; n counts the rest. Every case here is finite:
#   a box plot of infinite values has no meaning, and Curvis leaves them out too.
# - Quartiles and median: stats::quantile with type = 6, the (n + 1)p definition used
#   by Prism, SPSS and Minitab. The median is the same for every type.
# - Whiskers: from the minimum to the maximum.
# - n = 0: every statistic is NaN. n = 1: all five are the single value.

box_row <- function(values) {
  x <- values[!is.na(values)]
  n <- length(x)
  if (n == 0L) {
    return(list(n = 0L, min = NaN, q1 = NaN, median = NaN, q3 = NaN, max = NaN))
  }
  q <- unname(quantile(x, probs = c(0.25, 0.5, 0.75), type = 6, names = FALSE))
  stopifnot(q[2] == median(x))
  list(n = n, min = min(x), q1 = q[1], median = q[2], q3 = q[3], max = max(x))
}

# groups: list of numeric vectors (one per box), which may differ in length.
box_fixture <- function(case, description, groups) {
  groups <- lapply(groups, as.double)
  stats <- lapply(groups, box_row)
  column <- function(name) vapply(stats, function(s) as.double(s[[name]]), numeric(1))
  write_fixture(
    analysis = "box_stats",
    case = case,
    description = description,
    inputs = list(groups = groups),
    expected = list(
      n = as.integer(column("n")),
      min = column("min"),
      q1 = column("q1"),
      median = column("median"),
      q3 = column("q3"),
      max = column("max")
    ),
    kinds = c(n = "exact", min = "exact", q1 = "statistic", median = "statistic",
              q3 = "statistic", max = "exact"),
    packages = "stats"
  )
}

# --- Cases -------------------------------------------------------------------------

box_fixture(
  "basic", "Four normal groups of different sizes (5 to 40 values).",
  list(rnorm(5, 10, 2), rnorm(12, 50, 10), rnorm(23, -3, 0.5), rnorm(40, 1e3, 150))
)

box_fixture(
  "n_small", "n = 0, 1, 2, 3 and 4, where the quartiles fall on or outside the values.",
  list(numeric(0), c(4.2), c(5, 7), c(-1, 3, 8), c(1, 2, 4, 8))
)

box_fixture(
  "every_n_mod_4", "n = 5 to 12, so (n + 1)p hits whole and fractional positions.",
  lapply(5:12, function(n) round(rexp(n, 0.2), 3))
)

box_fixture(
  "ties", "Repeated values, including ties at the quartile positions.",
  list(c(2, 2, 3, 3), c(1, 1, 1, 5, 5), c(0.1, 0.1, 0.2, 0.2, 0.2, 0.3),
       c(-3, -3, -3, 7, 7, 7, 7), rep(c(1, 2, 3), each = 4))
)

box_fixture(
  "missing", "Groups with missing values (NaN), which are left out.",
  list(c(1, NaN, 3, 4, NaN, 6), c(NaN, NaN, 2.5), c(NaN, NaN, NaN),
       local({ x <- rnorm(20, 5, 1); x[sample(20, 7)] <- NaN; x }))
)

box_fixture(
  "unbalanced", "Groups of 3, 50 and 200 values.",
  list(c(0.5, 0.7, 0.2), rgamma(50, 2, 1), rnorm(200, -40, 12))
)

box_fixture(
  "constant", "Equal values: every statistic is that value.",
  list(rep(3.3, 6), rep(-7.77, 2), rep(0, 9), rep(1e300, 4))
)

box_fixture(
  "large_offset", "Small spread on a large offset.",
  list(1e9 + c(0.1, 0.2, 0.3, 0.4, 0.5), 1e15 + c(1, 2, 3, 4, 5, 6, 7),
       1e-12 * c(3, 1, 4, 1, 5, 9, 2, 6))
)
