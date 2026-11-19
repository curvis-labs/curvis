# Violin-plot densities: for each group of values, the bandwidth, the range the violin
# covers (trimmed or not) and the kernel density estimate on an even grid over it.
#
# Curvis: curvis.charts.data.violin_density (the outlines of violin plots).
#
# Definitions:
# - Missing values (NaN) are left out; n counts the rest. A group needs n >= 2 for a
#   violin (stats::bw.nrd0 needs two values); Curvis draws only the median line of a
#   group with one value, so that case has no fixture here.
# - Kernel: Gaussian. Bandwidth: stats::bw.nrd0, R's default for density() (Silverman's
#   rule of thumb, 0.9 min(SD, IQR / 1.34) n^(-1/5), with its fall-backs when SD or IQR
#   is 0 -- constant data falls back to |x[1]|, then to 1).
# - Range: trimmed, from the smallest to the largest value; not trimmed, 3 bandwidths
#   further on each side (density()'s default cut = 3).
# - Grid: 101 evenly spaced points from the low to the high end (both included).
# - Density: the exact sum (1 / n) sum_i dnorm(g, x_i, bw) at each grid point g.
#   density() itself bins the values and uses an FFT, an approximation good to about
#   1e-3; the exact sum is computed here so the fixture can hold 1e-8. Each case checks
#   that density() agrees with the exact sum to 1e-2 of its peak, so the definition is
#   the one density() approximates.

GRID_POINTS <- 101L
CUT <- 3

violin_row <- function(values) {
  x <- values[!is.na(values)]
  n <- length(x)
  stopifnot(n >= 2L)
  bw <- bw.nrd0(x)
  exact <- function(grid) vapply(grid, function(g) sum(dnorm(g, x, bw)) / n, numeric(1))

  trimmed_grid <- seq(min(x), max(x), length.out = GRID_POINTS)
  full_grid <- seq(min(x) - CUT * bw, max(x) + CUT * bw, length.out = GRID_POINTS)
  trimmed <- exact(trimmed_grid)
  full <- exact(full_grid)

  # density() approximates the same estimate over the untrimmed range.
  d <- density(x, bw = bw, kernel = "gaussian", cut = CUT, n = 512L)
  stopifnot(max(abs(approx(d$x, d$y, full_grid, rule = 2)$y - full)) < 1e-2 * max(full))

  list(n = n, bw = bw,
       trimmed_low = min(x), trimmed_high = max(x),
       full_low = min(x) - CUT * bw, full_high = max(x) + CUT * bw,
       trimmed_density = trimmed, full_density = full)
}

# groups: list of numeric vectors (one per violin), which may differ in length.
violin_fixture <- function(case, description, groups) {
  groups <- lapply(groups, as.double)
  rows <- lapply(groups, violin_row)
  column <- function(name) vapply(rows, function(r) as.double(r[[name]]), numeric(1))
  write_fixture(
    analysis = "violin_density",
    case = case,
    description = description,
    inputs = list(groups = groups, grid_points = scalar(GRID_POINTS), cut = scalar(CUT)),
    expected = list(
      n = as.integer(column("n")),
      bw = column("bw"),
      trimmed_low = column("trimmed_low"),
      trimmed_high = column("trimmed_high"),
      full_low = column("full_low"),
      full_high = column("full_high"),
      trimmed_density = lapply(rows, function(r) r$trimmed_density),
      full_density = lapply(rows, function(r) r$full_density)
    ),
    kinds = c(n = "exact", bw = "statistic", trimmed_low = "exact", trimmed_high = "exact",
              full_low = "statistic", full_high = "statistic",
              trimmed_density = "statistic", full_density = "statistic"),
    packages = "stats"
  )
}

# --- Cases -------------------------------------------------------------------------

violin_fixture(
  "basic", "Four groups of different sizes and shapes (8 to 60 values).",
  list(rnorm(8, 10, 2), rnorm(25, 50, 10), rgamma(40, 2, 1),
       c(rnorm(30, 0, 1), rnorm(30, 5, 1)))
)

violin_fixture(
  "n_two", "n = 2 and n = 3, the smallest groups that get a violin.",
  list(c(5, 7), c(-1, 3), c(-1, 3, 8))
)

violin_fixture(
  "ties", "Repeated values; one group whose IQR is 0 while its SD is not.",
  list(c(2, 2, 3, 3), c(1, 1, 1, 5, 5), rep(c(1, 2, 3), each = 4), c(4, 4, 4, 4, 4, 9))
)

violin_fixture(
  "missing", "Groups with missing values (NaN), which are left out.",
  list(c(1, NaN, 3, 4, NaN, 6), c(NaN, 2.5, NaN, 4),
       local({ x <- rnorm(20, 5, 1); x[sample(20, 7)] <- NaN; x }))
)

violin_fixture(
  "unbalanced", "Groups of 3, 50 and 200 values.",
  list(c(0.5, 0.7, 0.2), rgamma(50, 2, 1), rnorm(200, -40, 12))
)

violin_fixture(
  "constant", "Equal values: SD and IQR are 0, so bw.nrd0 falls back to |x[1]| or 1.",
  list(rep(3.3, 6), rep(-7.77, 2), rep(0, 9))
)

violin_fixture(
  "large_offset", "Small spread on a large offset, and a very small scale.",
  list(1e9 + c(0.1, 0.2, 0.3, 0.4, 0.5), 1e-12 * c(3, 1, 4, 1, 5, 9, 2, 6))
)
