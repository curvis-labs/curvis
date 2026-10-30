# Writes validation fixtures: JSON files with the inputs, the expected outputs computed
# by R, and the R and package versions that produced them.
#
# Fixtures go to tests/validation/fixtures/<analysis>/<case>.json and are read by
# tests/validation/harness.py. The format:
#
#   {
#     "schema_version": 1,
#     "analysis": "descriptive_stats",      # selects the Python adapter
#     "case": "basic",
#     "description": "...",
#     "generated_by": {"script": ..., "r_version": ..., "platform": ..., "packages": {...}},
#     "inputs":   {...},                    # arguments for the Curvis function
#     "expected": {"mean": [...], ...},     # outputs computed by R
#     "kinds":    {"mean": "statistic", ...} # tolerance class of each output
#   }
#
# Numbers are written with 17 significant digits, which always identify a double
# exactly (R's own number parser is not exact on every platform, so it cannot be used
# to find a shorter form), so inputs and results reach Python exactly. JSON has no NaN or infinity:
# NaN and NA are written as "NaN" (Curvis has one marker for missing and undefined
# values), infinities as "Inf" and "-Inf". There is no timestamp, so regenerating with
# the same R and packages gives identical files.

FIXTURE_SCHEMA_VERSION <- 1L

# Tolerance classes; the values are in tests/validation/tolerances.py (CLAUDE.md).
FIXTURE_KINDS <- c("exact", "statistic", "p_value", "parameter", "ci")

# Set by generate_all.R to the script being run (path relative to the repository root).
.fixture_state <- new.env()
.fixture_state$script <- NA_character_
.fixture_state$written <- character()

fixture_dir <- function() {
  file.path(r_project_dir(), "..", "..", "tests", "validation", "fixtures")
}

# Marks a value to be written as a JSON scalar instead of an array.
scalar <- function(x) {
  stopifnot(length(x) == 1L)
  structure(x, class = c("fixture_scalar", class(x)))
}

format_double <- function(x) {
  vapply(x, function(v) {
    if (is.na(v)) return('"NaN"')  # NA and NaN
    if (is.infinite(v)) return(if (v > 0) '"Inf"' else '"-Inf"')
    sprintf("%.17g", v)
  }, character(1))
}

format_atomic <- function(x) {
  if (is.logical(x)) {
    if (anyNA(x)) stop("logical NA cannot be written to a fixture")
    return(ifelse(x, "true", "false"))
  }
  if (is.integer(x)) {
    if (anyNA(x)) stop("integer NA cannot be written to a fixture; use a double NaN")
    return(sprintf("%d", x))
  }
  if (is.double(x)) return(format_double(x))
  if (is.character(x)) {
    return(vapply(x, function(s) as.character(jsonlite::toJSON(s, auto_unbox = TRUE)),
                  character(1), USE.NAMES = FALSE))
  }
  stop("cannot write values of type ", typeof(x), " to a fixture")
}

# Serializes nested named lists (objects), unnamed lists (arrays) and atomic vectors.
# Atomic vectors are arrays, except single strings and values wrapped in scalar().
# A matrix is written as a list of its columns.
to_json <- function(x, indent = 0L) {
  pad <- strrep("  ", indent + 1L)
  end <- strrep("  ", indent)
  if (is.matrix(x)) {
    x <- lapply(seq_len(ncol(x)), function(j) x[, j])
  }
  if (is.list(x)) {
    if (length(x) == 0L) return(if (is.null(names(x))) "[]" else "{}")
    items <- vapply(x, to_json, character(1), indent = indent + 1L, USE.NAMES = FALSE)
    if (is.null(names(x))) {
      return(paste0("[\n", paste0(pad, items, collapse = ",\n"), "\n", end, "]"))
    }
    if (any(names(x) == "") || anyDuplicated(names(x))) {
      stop("object keys must be unique and non-empty")
    }
    keys <- format_atomic(names(x))
    return(paste0("{\n", paste0(pad, keys, ": ", items, collapse = ",\n"), "\n", end, "}"))
  }
  values <- format_atomic(unclass(x))
  if (inherits(x, "fixture_scalar") || (is.character(x) && length(x) == 1L)) {
    return(values)
  }
  paste0("[", paste(values, collapse = ", "), "]")
}

package_versions <- function(packages) {
  packages <- sort(unique(c(packages, "jsonlite")))
  versions <- lapply(packages, function(p) as.character(utils::packageVersion(p)))
  names(versions) <- packages
  versions
}

# Writes one fixture.
#
# - analysis: name of the Python adapter (tests/validation/adapters.py).
# - case: file name, e.g. "n_small".
# - inputs: named list of arguments for the Curvis function.
# - expected: named list of outputs computed with R.
# - kinds: named character vector, the tolerance class of each output.
# - packages: R packages the script used to compute `expected` (base R is covered by
#   the R version, but list e.g. "stats" for clarity).
write_fixture <- function(analysis, case, description, inputs, expected, kinds,
                          packages = character()) {
  stopifnot(
    grepl("^[a-z0-9_]+$", analysis), grepl("^[a-z0-9_]+$", case),
    is.list(inputs), !is.null(names(inputs)),
    is.list(expected), !is.null(names(expected)),
    setequal(names(kinds), names(expected)),
    all(kinds %in% FIXTURE_KINDS)
  )
  if (is.na(.fixture_state$script)) stop("write_fixture() must be called from generate_all.R")
  fixture <- list(
    schema_version = scalar(FIXTURE_SCHEMA_VERSION),
    analysis = analysis,
    case = case,
    description = description,
    generated_by = list(
      script = .fixture_state$script,
      r_version = R.version.string,
      platform = R.version$platform,
      packages = package_versions(packages)
    ),
    inputs = inputs,
    expected = expected,
    kinds = as.list(kinds[names(expected)])
  )
  dir <- file.path(fixture_dir(), analysis)
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  path <- file.path(dir, paste0(case, ".json"))
  key <- paste0(analysis, "/", case)
  if (key %in% .fixture_state$written) stop("fixture written twice: ", key)
  con <- file(path, open = "wb")  # binary: "\n" line endings on every platform
  on.exit(close(con))
  writeLines(to_json(fixture), con, sep = "\n", useBytes = TRUE)
  .fixture_state$written <- c(.fixture_state$written, key)
  invisible(path)
}
