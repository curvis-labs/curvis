# Histogram bins: the bin edges and, for each group of values, the count in each bin.
#
# Curvis: curvis.charts.data.histogram_bins (histograms in the Bar family).
#
# Definitions:
# - Missing values (NaN) are left out. All groups of a graph share one set of bins,
#   computed from their values pooled.
# - Bins hold their right edge, and the first bin also its left edge: (a, b], with
#   [a, b] for the first (graphics::hist's right = TRUE, include.lowest = TRUE).
# - Bin choice, as in the Format dialog:
#   - Auto: hist()'s default, base::pretty(range(x), n = nclass.Sturges(x), min.n = 1).
#   - A number of bins k: exactly k equal bins from the smallest to the largest value
#     (seq(min, max, length.out = k + 1)). hist(breaks = k) takes k only as a hint for
#     pretty(), which is not what a typed number of bins promises. When every value is
#     the same (no range to split) the Auto bins are used instead.
#   - A bin width w from a start s: edges s, s + w, s + 2w ... up to the first edge at or
#     above the largest value. Values below s are left out and counted in n_outside.
# - Counts use cut() and are checked against hist()$counts wherever hist() accepts the
#   edges (they span every value).

auto_breaks <- function(x) pretty(range(x), n = nclass.Sturges(x), min.n = 1)

count_bins <- function(values, breaks) {
  x <- values[!is.na(values)]
  bins <- cut(x, breaks, right = TRUE, include.lowest = TRUE)
  counts <- as.integer(table(bins))
  if (length(x) > 0L && min(x) >= breaks[1] && max(x) <= breaks[length(breaks)]) {
    stopifnot(identical(counts, as.integer(hist(x, breaks = breaks, plot = FALSE)$counts)))
  }
  list(counts = counts, n_outside = as.integer(sum(is.na(bins))))
}

histogram_fixture <- function(case, description, groups, bins = "auto", start = NULL,
                              width = NULL, count = NULL) {
  groups <- lapply(groups, as.double)
  pooled <- unlist(groups)
  pooled <- pooled[!is.na(pooled)]
  breaks <- switch(
    bins,
    auto = auto_breaks(pooled),
    count = if (min(pooled) == max(pooled)) auto_breaks(pooled) else {
      b <- seq(min(pooled), max(pooled), length.out = count + 1L)
      b[c(1L, count + 1L)] <- c(min(pooled), max(pooled))
      b
    },
    width = {
      m <- max(1, ceiling((max(pooled) - start) / width))
      if (start + m * width < max(pooled)) m <- m + 1
      start + width * (0:m)
    }
  )
  rows <- lapply(groups, count_bins, breaks = breaks)
  inputs <- list(groups = groups, bins = bins)
  if (!is.null(count)) inputs$count <- scalar(as.integer(count))
  if (!is.null(width)) inputs$width <- scalar(as.double(width))
  if (!is.null(start)) inputs$start <- scalar(as.double(start))
  write_fixture(
    analysis = "histogram_bins",
    case = case,
    description = description,
    inputs = inputs,
    expected = list(
      breaks = breaks,
      counts = lapply(rows, function(r) r$counts),
      n_outside = vapply(rows, function(r) r$n_outside, integer(1))
    ),
    kinds = c(breaks = "statistic", counts = "exact", n_outside = "exact"),
    packages = c("graphics", "stats")
  )
}

# --- Cases -------------------------------------------------------------------------

histogram_fixture(
  "auto_basic", "Auto bins of two normal groups (30 and 80 values).",
  list(rnorm(30, 10, 2), rnorm(80, 12, 3))
)

histogram_fixture(
  "auto_skewed", "Auto bins of one skewed group of 500 values.",
  list(rexp(500, 0.3))
)

histogram_fixture(
  "auto_n_two", "n = 2: the smallest histogram.",
  list(c(1.5, 4.25))
)

histogram_fixture(
  "auto_ties_on_edges", "Values on the bin edges, which belong to the bin on their left.",
  list(c(0, 1, 1, 2, 2, 2, 3, 4, 5, 5, 6, 8, 10))
)

histogram_fixture(
  "auto_missing", "Groups with missing values (NaN), which are left out.",
  list(c(1, NaN, 3, 4, NaN, 6, 2.5), c(NaN, NaN, 7, 0.5))
)

histogram_fixture(
  "auto_unbalanced", "Groups of 3, 50 and 400 values.",
  list(c(0.5, 0.7, 0.2), rgamma(50, 2, 1), rnorm(400, 3, 1))
)

histogram_fixture(
  "auto_constant", "Equal values: pretty() widens a zero range.",
  list(rep(3.3, 6))
)

histogram_fixture(
  "auto_large_offset", "Small spread on a large offset, and a very small scale.",
  list(1e9 + c(0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.61))
)

histogram_fixture(
  "auto_tiny_scale", "Values of the order of 1e-12.",
  list(1e-12 * c(3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5))
)

histogram_fixture(
  "count_basic", "Exactly 7 bins of two groups.",
  list(rnorm(40, 0, 1), rnorm(25, 1, 1.5)), bins = "count", count = 7L
)

histogram_fixture(
  "count_one", "A single bin.",
  list(c(2, 3, 5, 8)), bins = "count", count = 1L
)

histogram_fixture(
  "count_constant", "Equal values with a number of bins: the Auto bins are used.",
  list(rep(-2, 5)), bins = "count", count = 5L
)

histogram_fixture(
  "width_basic", "Bins 0.5 wide from 0.",
  list(c(0.1, 0.5, 0.6, 1.0, 1.2, 2.49, 2.5, 3.0)), bins = "width", width = 0.5, start = 0
)

histogram_fixture(
  "width_start_above_min", "A start above the smallest values: they are left out.",
  list(c(-3, -1, 0.5, 2, 4, 6.5), c(-0.5, 1, 9)), bins = "width", width = 2, start = 0
)

histogram_fixture(
  "width_max_on_edge", "The largest value lies on an edge.",
  list(c(1, 2, 3, 4, 5, 6)), bins = "width", width = 1, start = 1
)
