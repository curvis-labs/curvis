# Regenerates every validation fixture in tests/validation/fixtures/.
#
#   Rscript validation/r/generate_all.R
#
# Runs from any working directory. Uses the renv project in validation/r (run
# renv::restore() there once to install the locked package versions). Each script in
# validation/r/scripts/ runs in its own environment and writes its fixtures with
# write_fixture() from R/fixture.R. Exits with status 1 if any script fails.

local({
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- sub("^--file=", "", args[grep("^--file=", args)])
  if (length(file_arg) != 1L) stop("run this file with Rscript")
  dir <- normalizePath(dirname(file_arg), winslash = "/", mustWork = TRUE)
  assign("r_project_dir", function() dir, envir = globalenv())
})

# English messages in any locale, so logs and errors read the same everywhere.
Sys.setenv(LANGUAGE = "en")

owd <- setwd(r_project_dir())
source("renv/activate.R")
setwd(owd)

source(file.path(r_project_dir(), "R", "fixture.R"))

repo_root <- normalizePath(file.path(r_project_dir(), "..", ".."), winslash = "/")
scripts <- sort(list.files(file.path(r_project_dir(), "scripts"), pattern = "\\.R$",
                           full.names = TRUE))
if (length(scripts) == 0L) stop("no scripts in validation/r/scripts")

failed <- character()
for (script in scripts) {
  relative <- substring(normalizePath(script, winslash = "/"), nchar(repo_root) + 2L)
  .fixture_state$script <- relative
  before <- length(.fixture_state$written)
  # Same seed and RNG for every script, so each one is reproducible on its own.
  RNGkind("Mersenne-Twister", "Inversion", "Rejection")
  set.seed(20261030L)
  ok <- tryCatch({
    sys.source(script, envir = new.env(parent = globalenv()))
    TRUE
  }, error = function(e) {
    message("FAILED ", relative, ": ", conditionMessage(e))
    FALSE
  })
  if (ok) {
    cat(sprintf("%-50s %d fixtures\n", relative, length(.fixture_state$written) - before))
  } else {
    failed <- c(failed, relative)
  }
}

on_disk <- sub("\\.json$", "", list.files(fixture_dir(), pattern = "\\.json$", recursive = TRUE))
stale <- setdiff(on_disk, .fixture_state$written)
if (length(failed) == 0L && length(stale) > 0L) {
  message("Fixtures no script wrote (delete them if their case was removed):\n  ",
          paste(stale, collapse = "\n  "))
}

cat(sprintf("%d fixtures written by %d scripts with %s.\n",
            length(.fixture_state$written), length(scripts) - length(failed),
            R.version.string))
if (length(failed) > 0L) {
  message(length(failed), " script(s) failed.")
  quit(status = 1L)
}
