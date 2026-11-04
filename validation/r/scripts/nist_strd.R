# NIST Statistical Reference Datasets (StRD): the certified values of the linear
# regression datasets that Curvis' models cover, and of the univariate nonlinear
# regression datasets.
#
# Curvis: curvis.analysis.fit (the linear solver, and the nonlinear engine with each
# NIST model defined in tests/validation).
#
# Expected values are NIST's certified values, not R results; R only reads them. The
# .dat files are downloaded once from https://www.itl.nist.gov/div898/strd/ into
# validation/r/nist/ (committed, so later runs work offline) and parsed with the line
# ranges that each file states in its header.
#
# Linear datasets (analysis "nist_linear"): Norris (line), Pontius (quadratic) and
# Wampler1-5 (degree 5). Not used: Filip (degree 10; Curvis goes to degree 6), NoInt1/2
# (no intercept) and Longley (several predictors). Outputs: coefficients (B0, B1, ...),
# their certified SDs (se), the residual SD (sy_x) and R². Wampler1 and Wampler2 are
# exact fits whose certified SDs are 0; a purely relative comparison would need exact
# zeros, so their se and sy_x are left out.
#
# Nonlinear datasets (analysis "nist_nls"): all with one predictor (Nelson has two and
# is left out). One fixture per dataset and NIST starting point ("start1" is far from
# the solution, "start2" close). Outputs: parameters (b1, b2, ...), their certified SDs,
# the residual sum of squares and residual SD, and df.

NIST_DIR <- file.path(r_project_dir(), "nist")
NIST_URL <- "https://www.itl.nist.gov/div898/strd/%s/data/LINKS/DATA/%s.dat"

nist_lines <- function(kind, name) {
  dir.create(NIST_DIR, showWarnings = FALSE)
  path <- file.path(NIST_DIR, paste0(name, ".dat"))
  if (!file.exists(path)) {
    utils::download.file(sprintf(NIST_URL, kind, name), path, mode = "wb", quiet = TRUE)
  }
  sub("\r$", "", readLines(path, warn = FALSE))
}

# The line numbers of a section, from the header line "<label> (lines a to b)".
section_range <- function(lines, label) {
  hit <- grep(paste0(label, "\\s*\\(lines\\s+\\d+\\s+to\\s+\\d+\\)"), lines, value = TRUE,
              ignore.case = TRUE)
  if (length(hit) != 1L) stop("no single '", label, " (lines a to b)' line in the header")
  m <- regmatches(hit, regexec("lines\\s+(\\d+)\\s+to\\s+(\\d+)", hit))[[1]]
  as.integer(m[2]):as.integer(m[3])
}

section <- function(lines, label) lines[section_range(lines, label)]

numbers <- function(line) {
  tokens <- strsplit(trimws(line), "\\s+")[[1]]
  as.double(tokens[grepl("^[-+]?[0-9.]+([EeDd][-+]?[0-9]+)?$", tokens)])
}

# The data section: numeric rows. The column names are on the last non-blank line
# above the first data row ("Data:   y   x" in the StRD files; the "Data:" is optional).
nist_data <- function(lines) {
  range <- section_range(lines, "Data")
  rows <- lines[range]
  rows <- rows[nzchar(trimws(rows))]
  m <- do.call(rbind, lapply(rows, numbers))
  above <- which(nzchar(trimws(lines[seq_len(range[1] - 1L)])))
  header <- lines[max(above)]
  cols <- strsplit(trimws(sub("^\\s*Data:", "", header)), "\\s+")[[1]]
  if (ncol(m) != length(cols)) {
    stop("data columns: ", ncol(m), " numbers per row, but the line above the data reads '",
         trimws(header), "'")
  }
  colnames(m) <- tolower(cols)
  m
}

# The number after `label` on the one line where a number follows it.
labelled_value <- function(lines, label) {
  hit <- grep(paste0(label, "\\s+[-+]?[0-9.]"), lines, value = TRUE)
  stopifnot(length(hit) == 1L)
  v <- numbers(sub(paste0(".*", label), "", hit))
  stopifnot(length(v) >= 1L)
  v[1]
}

# --- Linear --------------------------------------------------------------------------

nist_linear <- function(name, degree) {
  withCallingHandlers(nist_linear_(name, degree), error = function(e) {
    message("While reading ", name, ".dat (NIST StRD linear)")
  })
}

nist_linear_ <- function(name, degree) {
  lines <- nist_lines("lls", name)
  d <- nist_data(lines)
  stopifnot(identical(colnames(d), c("y", "x")))
  cert <- section(lines, "Certified Values")
  rows <- grep("^\\s*B\\d+\\s", cert, value = TRUE)
  stopifnot(length(rows) == degree + 1L)
  v <- do.call(rbind, lapply(rows, numbers))
  expected <- list(coefficients = v[, 1], se = v[, 2],
                   sy_x = labelled_value(cert, "Standard Deviation"),
                   r_squared = labelled_value(cert, "R-Squared"))
  kinds <- c(coefficients = "parameter", se = "parameter", sy_x = "statistic",
             r_squared = "statistic")
  note <- ""
  if (any(expected$se == 0)) {
    expected$se <- NULL
    expected$sy_x <- NULL
    kinds <- kinds[c("coefficients", "r_squared")]
    note <- " Exact fit: the certified SDs are 0 and are not compared."
  }
  write_fixture(
    analysis = "nist_linear",
    case = tolower(name),
    description = paste0("NIST StRD linear regression dataset ", name, ", degree ", degree,
                         ", certified values.", note),
    inputs = list(dataset = name, x = d[, "x"], y = d[, "y"],
                  degree = scalar(as.integer(degree))),
    expected = expected,
    kinds = kinds
  )
}

nist_linear("Norris", 1L)
nist_linear("Pontius", 2L)
for (i in 1:5) nist_linear(paste0("Wampler", i), 5L)

# --- Nonlinear -----------------------------------------------------------------------

nist_nls <- function(name) {
  withCallingHandlers(nist_nls_(name), error = function(e) {
    message("While reading ", name, ".dat (NIST StRD nonlinear)")
  })
}

nist_nls_ <- function(name) {
  lines <- nist_lines("nls", name)
  d <- nist_data(lines)
  stopifnot(identical(colnames(d), c("y", "x")))
  start <- section(lines, "Starting Values")
  rows <- grep("^\\s*b\\d+\\s*=", start, value = TRUE)
  v <- do.call(rbind, lapply(rows, function(r) numbers(sub("^\\s*b\\d+\\s*=", "", r))))
  stopifnot(ncol(v) == 4L)  # start 1, start 2, certified value, certified SD
  cert <- section(lines, "Certified Values")
  rss <- labelled_value(cert, "Residual Sum of Squares:")
  sy_x <- labelled_value(cert, "Residual Standard Deviation:")
  df <- labelled_value(cert, "Degrees of Freedom:")
  stopifnot(df == nrow(d) - nrow(v))
  for (s in 1:2) {
    write_fixture(
      analysis = "nist_nls",
      case = paste0(tolower(name), "_start", s),
      description = paste0("NIST StRD nonlinear regression dataset ", name,
                           " from starting values ", s, ", certified values."),
      inputs = list(dataset = name, x = d[, "x"], y = d[, "y"], start = v[, s],
                    start_number = scalar(as.integer(s))),
      expected = list(parameters = v[, 3], se = v[, 4], rss = scalar(rss),
                      sy_x = scalar(sy_x), df = scalar(as.integer(df))),
      kinds = c(parameters = "parameter", se = "parameter", rss = "statistic",
                sy_x = "statistic", df = "exact")
    )
  }
}

for (name in c(
  # lower difficulty
  "Misra1a", "Chwirut2", "Chwirut1", "Lanczos3", "Gauss1", "Gauss2", "DanWood", "Misra1b",
  # average difficulty
  "Kirby2", "Hahn1", "MGH17", "Lanczos1", "Lanczos2", "Gauss3", "Misra1c", "Misra1d",
  "Roszman1", "ENSO",
  # higher difficulty
  "MGH09", "Thurber", "BoxBOD", "Rat42", "MGH10", "Eckerle4", "Rat43", "Bennett5"
)) nist_nls(name)
