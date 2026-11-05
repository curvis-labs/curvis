# Curvis — build phases and Claude Code prompts

Curvis is built in 5 phases and 28 steps. Each step is one Claude Code session that ends in a working, tested state. Each phase ends with a gate you check yourself before moving on.

| Phase | Steps | Ends with (gate) |
| --- | --- | --- |
| 0 Foundations | 0.1–0.5 | App starts on Windows, macOS and Linux from CI-built installers |
| 1 Data tables | 1.1–1.6 | You can type, paste, import and calculate data comfortably; dialogs follow one design |
| 2 Core analyses + MVP | 2.1–2.9 | MVP beta: core tests match R, speed targets met |
| 3 Full outline | 3.1–3.6 | Every feature in SPEC.md works |
| 4 Release 1.0 | 4.1–4.2 | Validation report published, 1.0 installers released |

## How to use this file

1. Create an empty Git repository named `curvis`.
2. Put `CLAUDE.md` in the repository root, and `SPEC.md` and this file (`PHASES.md`) in `docs/`.
3. Install `uv`, Git and R (R is needed from step 2.1 on, to generate reference results).
4. For each step: start a fresh Claude Code session in the repository, switch to plan mode, paste the step's prompt, review the plan, then let it implement.
5. When a step reports done, check the "Done when" list yourself. Statistics steps also need you to review the R script before implementation starts.
   **Generate fixtures locally with R 4.6.1.** Cloud sessions cannot install it (CRAN is blocked; Ubuntu only has R 4.3, whose `wilcox.test` treats ties differently). Run the R fixture scripts on your own machine with the renv project in `validation/r`, then commit `renv.lock` and the fixtures. Claude Code can write and test-run the scripts in the cloud, but never commits fixtures made with another R version.
6. Merge the branch, tick the step below, and move on.

Every prompt follows the same pattern: read the rules, confirm open decisions, plan, build with tests, report. Where a prompt lists **decisions**, Claude Code should ask you about them before writing code.

---

## Phase 0 — Foundations

### Step 0.1 — Repository scaffold and CI

**Done when**
- [x] `uv run curvis` opens an empty Qt window on your machine.
- [x] CI runs lint, mypy and pytest on Windows, macOS and Linux and is green.
- [x] `LICENSE` file exists with the license you chose.

```text
Read CLAUDE.md, docs/SPEC.md and step 0.1 in docs/PHASES.md.

Set up the Curvis repository from scratch:
- pyproject.toml managed by uv, Python 3.12, package in src/curvis with the folder layout from CLAUDE.md (empty modules with docstrings are fine).
- Entry point `curvis` that opens an empty PySide6 main window titled "Curvis".
- ruff (lint + format), mypy, pytest, pytest-qt, hypothesis, pytest-benchmark configured.
- pre-commit hooks for ruff and mypy.
- GitHub Actions workflow running lint, mypy and tests on ubuntu-latest, macos-latest and windows-latest (Qt tests headless).
- README with one paragraph about Curvis and the dev commands from CLAUDE.md.

Decisions to confirm with me before coding:
- License: GPL-3.0 or MIT?

Make a plan first. When done, report what you built and the test results.
```

### Step 0.2 — Document model, commands, undo and change log

**Done when**
- [x] A document with tables and columns can be created and changed only through commands.
- [x] Undo/redo works up to 50 steps; the change log records every command.
- [x] Dependency graph returns the correct stale set and recompute order (tested, including cycles being rejected).

```text
Read CLAUDE.md, docs/SPEC.md and step 0.2 in docs/PHASES.md.

Build the core document model in src/curvis/core and src/curvis/data, with no UI:
- Dataclasses for Document, Table, Column (name, role: X/Y/Error/Time/Event/Text, unit, NumPy array, optional group id), ReplicateGroup and CategoryGroup. Missing values are NaN.
- Command base class with do()/undo(); concrete commands for: add/remove/rename table, add/remove/move/rename column, set cell values (single and block), set column role.
- UndoStack per document, max 50 steps, with signals or callbacks for "changed".
- ChangeLog: append-only list of entries (UTC timestamp, command type, short summary), filled automatically when a command executes.
- DependencyGraph: nodes declare what they read; given a changed node, return downstream nodes in topological order; reject cycles with a clear error.
- Thorough unit tests, including hypothesis tests that random command sequences followed by full undo restore the original document.

Make a plan first. When done, report what you built and the test results.
```

### Step 0.3 — `.cvp` project file format

**Done when**
- [x] Save then open returns an identical document (tested with hypothesis).
- [x] An interrupted save never corrupts an existing file.
- [x] A 10 MB project opens in under 1 s (benchmark).

```text
Read CLAUDE.md, docs/SPEC.md and step 0.3 in docs/PHASES.md.

Implement the .cvp project format in src/curvis/io/project.py:
- Zip container with project.json (all definitions, project constants, settings, change log, schema_version = 1) and one Arrow IPC file per table.
- save(document, path): write to a temp file in the same folder, fsync, then atomic rename.
- load(path) -> Document, with a migration hook keyed on schema_version (a no-op migration registry for now, plus a test showing how a migration is added).
- Clear errors for corrupt or newer-version files.
- Round-trip hypothesis tests, a test that simulates a crash mid-save, and a benchmark for opening a 10 MB project.

Make a plan first. When done, report what you built, test results and the benchmark number.
```

### Step 0.4 — Job scheduler and benchmark harness

**Done when**
- [x] Jobs run off the UI thread; newer jobs for the same node cancel older ones; edits are debounced.
- [x] Heavy jobs run in a process pool with arrays passed through shared memory.
- [x] `tests/bench` exists and CI fails on a >15% regression against a stored baseline.

```text
Read CLAUDE.md, docs/SPEC.md and step 0.4 in docs/PHASES.md.

Build src/curvis/compute:
- Scheduler with submit(node_id, job, weight="light"|"heavy"). Light jobs run on a thread pool, heavy jobs on a process pool; NumPy arrays go to processes through shared memory, not pickling.
- Debounce per node (default 150 ms, configurable), cancellation of superseded jobs, results delivered to the Qt main thread via a signal.
- Integration with the DependencyGraph from step 0.2: after a command, stale nodes are scheduled in dependency order.
- Error handling: a failing job reports its error to the node; it never crashes the app.
- Benchmark harness in tests/bench using pytest-benchmark, with a stored baseline file and a CI job that fails on a >15% slowdown.

Make a plan first. When done, report what you built and test and benchmark results.
```

### Step 0.5 — App shell, launcher, settings and installers

**Gate for phase 0:** installers from CI start the app on all three operating systems.

**Done when**
- [x] Launcher shows New project, Open and recent projects with relative timestamps.
- [x] Documents open as tabs; save/open/close with unsaved-changes prompts; undo/redo in the Edit menu.
- [x] Theme setting (light, dark, system) works; cold start to launcher < 1.5 s.
- [x] CI builds installers for Windows, macOS and Linux as artifacts.

```text
Read CLAUDE.md, docs/SPEC.md and step 0.5 in docs/PHASES.md.

Build the application shell in src/curvis/ui and src/curvis/settings:
- Launcher screen per SPEC.md (blank project, Open button, recent projects with relative timestamps like "2 days ago").
- Main window with one tab per document; File menu (New, Open, Save, Save As, Close), Edit menu (Undo, Redo) wired to the undo stack; unsaved-changes prompt on close.
- Command palette (Ctrl+K) listing all registered actions; every new action from now on registers there.
- Settings store (QSettings) with a Settings dialog: theme (light/dark/system), decimal places for calculated columns. Other settings from SPEC.md come in step 3.5.
- Lazy imports for heavy libraries; a benchmark for cold start to launcher.
- PyInstaller spec and CI job producing installers for Windows, macOS and Linux as build artifacts (signing comes in phase 4). Register the .cvp file extension where the platform allows.
- pytest-qt tests for the launcher, tabs and undo/redo menu.

Make a plan first. When done, report what you built, test results, the cold-start time, and which installers built.
```

---

## Phase 1 — Data tables

### Step 1.1 — Table view and editing

**Done when**
- [x] Typing, keyboard navigation, block selection, copy and paste to and from Excel work.
- [x] Every edit is undoable; column roles show in the header and can be changed.
- [x] Scrolling a 1,000,000-cell table stays smooth (60 fps benchmark).

```text
Read CLAUDE.md, docs/SPEC.md and step 1.1 in docs/PHASES.md.

Build the data table view in src/curvis/ui/table:
- QAbstractTableModel over the document's columns (reads NumPy arrays directly, only visible cells), QTableView with spreadsheet behaviour: typing, Enter/Tab/arrow navigation, block selection, Ctrl+C / Ctrl+V as tab-separated text compatible with Excel, Delete to clear.
- Every edit goes through commands (block paste is one command).
- Header shows column name and role; a header context menu adds, deletes, renames columns and changes the role.
- Number display honours the decimal-places setting for calculated columns.
- pytest-qt tests for editing, paste and undo; a benchmark that scrolls a 1,000,000-cell table and measures frame time.

Make a plan first. When done, report what you built, test results and the scroll benchmark.
```

### Step 1.2 — Replicate groups, category groups, drag and drop, bulk rename

**Done when**
- [x] Y columns can be grouped into replicate groups with mean, SD and SEM available to analyses and graphs.
- [x] Category groups define a second factor.
- [x] Columns and groups move by drag and drop; bulk rename works; all of it is undoable.

```text
Read CLAUDE.md, docs/SPEC.md and step 1.2 in docs/PHASES.md.

Implement column organisation from SPEC.md:
- Replicate groups: combine several Y columns into one series; provide a data-layer function returning per-row mean, SD, SEM and n (ignoring NaN), used later by analyses and graphs.
- Category groups: assign column groups to levels of a second named factor (for two-way ANOVA and grouped charts).
- Two-level header in the table showing groups above columns, with visual grouping.
- Drag and drop to reorder columns and whole groups.
- Bulk rename dialog: rename many columns and groups in one step (find/replace and numbered patterns), as a single undoable command.
- Unit tests for the data functions, pytest-qt tests for drag and drop and bulk rename.

Make a plan first. When done, report what you built and the test results.
```

### Step 1.3 — CSV/TSV import

**Done when**
- [x] Import preview shows the file, lets you pick header row and delimiter (auto, comma, semicolon, tab, space).
- [x] A 100,000-row CSV imports in under 1 s.
- [x] Odd files (BOM, quoted fields, decimal commas, empty cells) import correctly or give a clear message.

```text
Read CLAUDE.md, docs/SPEC.md and step 1.3 in docs/PHASES.md.

Build CSV/TSV import in src/curvis/io/importers.py and an Import dialog:
- Read with Polars; delimiter auto-detection plus manual choice (comma, semicolon, tab, space); header row selection; preview of the first 100 rows that updates live.
- Numeric columns become Y columns by default, the first numeric column X if the user ticks "First column is X"; text columns are kept as Text.
- Handle UTF-8 BOM, quoted fields, empty cells (NaN), and decimal commas when the delimiter is a semicolon.
- Import runs as a heavy job through the scheduler and is one undoable command.
- Fixture files in tests/data for the edge cases; a benchmark for a 100,000-row file.

Decisions to confirm with me before coding:
- Should the import dialog guess column roles beyond "first column is X"?

Make a plan first. When done, report what you built, test results and the benchmark.
```

### Step 1.4 — Formula engine and constants

**Done when**
- [x] Formulas with `[column]` references, + − × ÷ ^ %, FIRST, LAST, ROW(n), MIN, MAX, MEAN, SUM, COUNT and constants evaluate correctly.
- [x] Fuzzing shows the parser never executes code and never crashes.
- [x] A formula over 100,000 rows recalculates in under 50 ms.

```text
Read CLAUDE.md, docs/SPEC.md and step 1.4 in docs/PHASES.md.

Build src/curvis/formula with no UI:
- Lark grammar for the formula language in SPEC.md: [column] references (names with spaces allowed), numbers, + − × ÷ ^ and % (also accept * / for typing convenience), parentheses, unary minus, the functions FIRST, LAST, ROW(n), MIN, MAX, MEAN, SUM, COUNT, and named constants.
- Compile the parse tree to vectorised NumPy operations. Never use eval or exec.
- Built-in constants from SPEC.md with CODATA 2018 values and units, plus user constants stored per project (in the document) and globally (settings). Project constants override global ones.
- Clear error messages with the position of the problem (unknown column, unknown function, division by zero produces NaN not an error).
- Unit tests for every operator and function, hypothesis fuzzing of the parser, and a benchmark for 100,000 rows.

Decisions to confirm with me before coding:
- What does % mean: modulo, or "percent of" (x% = x/100)?
- Do aggregate functions (MEAN, SUM, ...) skip missing values?

Make a plan first. When done, report what you built, test results and the benchmark.
```

### Step 1.5 — Calculate dialog and live calculated columns

**Done when**
- [x] Baseline subtraction, normalize 0–100%, fold change and custom formula create calculated columns.
- [x] Calculated columns update automatically when their inputs change and can be edited later.
- [x] Constants can be managed in a dialog (project and global).

```text
Read CLAUDE.md, docs/SPEC.md and step 1.5 in docs/PHASES.md.

Build the Calculate dialog and calculated columns:
- CalculatedColumn definition stored in the document (formula text + target column), registered in the DependencyGraph so edits to inputs recompute it through the scheduler.
- Calculate dialog with the four options from SPEC.md. Baseline subtraction, normalize 0–100% and fold change are generated formulas, so the user can see and edit the formula afterwards.
- Editing a calculated column reopens the dialog with its definition.
- Calculated columns are visually distinct and read-only in the table.
- Constants dialog to add, edit and delete project and global constants.
- pytest-qt tests for each option, and a test that editing a source cell updates the calculated column.

Decisions to confirm with me before coding:
- Normalize 0–100%: normalise to the column's own min/max, or to user-chosen 0% and 100% values?

Make a plan first. When done, report what you built and the test results.
```

### Step 1.6 — UI design guidelines and dialog pass

**Gate for phase 1:** spend an hour entering, pasting, importing and calculating real data from your lab. Note anything awkward and fix it before phase 2.

**Done when**
- [x] docs/DESIGN.md describes dialog layout, spacing, colours, wording, feedback and keyboard use, and you have approved it.
- [x] You have approved mockups of the Analyze dialog, the Results panel and the Format dialog.
- [x] The existing dialogs follow DESIGN.md in light and dark themes, with nothing overlapping or cut off at their default size.

```text
Read CLAUDE.md, docs/SPEC.md and step 1.6 in docs/PHASES.md.

Write short UI design guidelines and bring the existing dialogs in line with them. Add no new features:
- docs/DESIGN.md, short and concrete:
  - Dialog layout, with the Calculate dialog as the reference: choices as tabs across the top, inputs on the left, options and result on the right, secondary settings in the button row, advanced options in a collapsed section. Say when a simple one-column dialog is enough.
  - Spacing, margins, default and minimum dialog sizes, and how panels resize (a stacked panel takes the height of the page shown, not of its tallest page).
  - Colours from palette roles only: which role is used for what (calculated columns, errors, warnings, role badges), with enough contrast in light and dark themes.
  - Wording: sentence case, verbs on buttons ("Calculate", "Import"), button order, mnemonics that do not clash within a dialog.
  - Feedback: where errors, assumption warnings and plain-language readings appear; a disabled OK always shows why; empty states.
  - Keyboard: tab order, shortcuts, command palette entries.
- A small shared module in src/curvis/ui for what DESIGN.md defines (dialog skeleton, spacing, stacked-page sizing), used by the existing dialogs.
- Mockups (an HTML canvas, not code) of the Analyze dialog, the Results panel and the Format dialog from SPEC.md, following DESIGN.md, for steps 2.2 and 2.8 to build from.
- Apply DESIGN.md to the Import, Bulk rename, Calculate, Constants and Settings dialogs. Take offscreen screenshots of each in light and dark theme, before and after.
- Add a line to the UI conventions in CLAUDE.md that points to docs/DESIGN.md.
- pytest-qt tests that every dialog fits its default size without overlapping widgets, and that every control has a label or an accessible name.

Decisions to confirm with me before coding:
- OK/Cancel order: follow each platform (Qt's default), or one fixed order everywhere?
- Which dialogs, besides Analyze, Results panel and Format, should get mockups now?

Make a plan first. Show me DESIGN.md and the mockups before changing any dialog. When done, report what changed in each dialog with the before and after screenshots, and the test results.
```

---

## Phase 2 — Core analyses and MVP

### Step 2.1 — Validation harness

**Done when**
- [x] `validation/r/` can generate JSON fixtures; `tests/validation` compares Python results to them with the CLAUDE.md tolerances.
- [x] You have reviewed the harness and one worked example (a simple mean/SD fixture).

```text
Read CLAUDE.md, docs/SPEC.md and step 2.1 in docs/PHASES.md.

Build the statistics validation harness:
- validation/r/: an R project (renv) with a helper that writes fixtures as JSON (inputs + expected outputs + R version + package versions), and generate_all.R that runs every fixture script.
- tests/validation/: a pytest plugin or helper that loads each fixture, runs the matching Curvis function, and compares outputs with the CLAUDE.md tolerances, giving a readable diff on failure.
- tests/validation/TOLERANCES.md, empty except for a header explaining that exceptions need my approval.
- One worked example: descriptive statistics (mean, SD, SEM, n, 95% CI of the mean) for replicate groups, R script first, then the Python function. Mean, SD, SEM and n per row already exist as `curvis.data.replicates.row_stats` (step 1.2, tested only against exact arithmetic so far): validate it against the R fixtures (edge cases: n = 0, 1, 2, NaN, ties, constant data, large offsets, infinities) and add only the 95% CI.
- A CI job that runs tests/validation (fixtures are committed, so CI does not need R).

Show me the R script before writing the Python side. Make a plan first. When done, report what you built and the test results.
```

### Step 2.2 — Analysis framework, Analyze dialog and Results panel

**Done when**
- [x] The Analyze dialog shows only analyses the table supports and opens on the best-fitting family.
- [x] Results appear in a Results panel with stars, plain-language reading and Ctrl+C copy.
- [x] Analyses rerun automatically when data changes and can be reopened to change parameters.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.2 in docs/PHASES.md.

Build the analysis framework, with no real statistics yet (use the descriptive statistics from step 2.1 as the first analysis):
- src/curvis/analysis/base.py: Analysis definition (type, source table id, parameters dataclass), a registry, a can_run(table) check, and a run(inputs, params) -> Result that is pure and runs in the scheduler.
- Result model: named values with labels and units, tables (e.g. post-hoc comparisons), significance stars (* < 0.05, ** < 0.01, *** < 0.001), warnings, and a one-line plain-language summary.
- Analyze dialog: families and analyses filtered by can_run, opening on survival / curve fits / compare groups per SPEC.md; parameters form with sensible defaults and an "Advanced" section.
- Results panel docked next to the table; Ctrl+C copies the selection as tab-separated text.
- Analyses are dependency-graph nodes: they rerun when source data changes and show a "stale" badge while running. Recently used analyses are remembered in settings.
- Follow docs/DESIGN.md and the Analyze dialog and Results panel mockups approved in step 1.6.
- pytest-qt tests for filtering, running, rerunning and copying.

Make a plan first. When done, report what you built and the test results.
```

### Step 2.3 — Compare groups: t-test, one-way ANOVA, Mann-Whitney

**Done when**
- [x] All three analyses match R fixtures, including edge cases.
- [x] One-way ANOVA (ordinary) offers Dunnett, Holm and Tukey post-hoc tests.

Note: the fixtures for this step were generated locally with R 4.6.1, after the cloud session found it could only install R 4.3. Do the same for every later step that needs R fixtures (see step 5 of "How to use this file").

```text
Read CLAUDE.md, docs/SPEC.md and step 2.3 in docs/PHASES.md.

Implement in src/curvis/analysis/compare:
- t-test, unpaired and paired, with mean difference and its confidence interval.
- One-way ANOVA, ordinary only (repeated measures comes in step 3.1), with post-hoc Dunnett (vs. a chosen control), Holm-corrected pairwise t-tests and Tukey HSD.
- Mann-Whitney U, with the exact or normal-approximation method chosen the same way R does it, and stated in the result.

Order of work:
1. Write R fixture scripts (stats::t.test, aov, TukeyHSD, multcomp::glht or DescTools::DunnettTest, pairwise.t.test with p.adjust "holm", wilcox.test) including edge cases: n = 2, ties, NaN, unequal group sizes, identical groups. Show me the scripts, then generate fixtures.
2. Implement with SciPy/statsmodels until tests/validation passes.
3. Plain-language summaries and assumption warnings (n < 3 per group, zero variance).

Decisions to confirm with me before coding:
- Unpaired t-test: offer Welch's correction as an option? Which is the default?
- Dunnett p-values are computed by numerical integration in both SciPy and R and differ slightly. Propose a tolerance for TOLERANCES.md and wait for my approval.

Make a plan first. When done, report validation results per analysis.
```

### Step 2.4 — Curve fits: linear, polynomial, 4PL, 3PL

**Done when**
- [x] Fits match R fixtures and applicable NIST StRD datasets.
- [x] Fits report parameters with standard errors and confidence intervals, R², and confidence bands.
- [x] X-range limits and fitting several series in one run work; 12 points × 20 series fit in under 200 ms.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.4 in docs/PHASES.md.

Implement in src/curvis/analysis/fit:
- A fit framework on lmfit: model definition, automatic data-driven initial guesses, parameter bounds and fixed parameters, X-range limits, multiple series per run, convergence checks with a clear "ambiguous fit" warning, and fitted-curve evaluation for graphs (with confidence bands).
- Linear regression and polynomials of degree 2–6 (slope, intercept, R², confidence bands).
- Hill 4PL with free Hill slope sign, and 3PL with bottom fixed at 0: report EC50, Hill slope, top, bottom with SE and 95% CI.

Order of work:
1. R fixture scripts (lm, predict with interval = "confidence", drc::drm with LL.4/LL.3 or minpack.lm::nlsLM) and the applicable NIST StRD nonlinear datasets. Include a non-converging case. Show me the scripts, then generate fixtures.
2. Implement until tests/validation passes.
3. Benchmark 12 points × 20 series.

Decisions to confirm with me before coding:
- Are X values for dose-response entered as concentrations or as log10(concentration)? Should the fit report EC50, logEC50, or both?
- Confidence intervals for nonlinear parameters: asymptotic (SE-based, like Prism's default) or profile likelihood?

Make a plan first. When done, report validation and benchmark results.
```

### Step 2.5 — Chart spec and on-screen graphs

**Done when**
- [x] XY (scatter/line with SD or SEM error bars and fitted curve overlay), bar and box graphs draw from a table.
- [x] Graphs redraw within 50 ms after a data edit; legend clicks toggle series on XY charts.
- [x] The main and table toolbars follow docs/DESIGN.md (section 7) and the approved toolbar mockup.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.5 in docs/PHASES.md.

Build src/curvis/charts:
- ChartSpec dataclasses: chart family and variant, series (data source, symbol, size, line style, width, color, error-bar type), axes (title, range, label, linear/log), title, aspect ratio, color scheme, text size, overlays (fitted curves from analyses).
- A builder that creates a sensible default spec from a table (replicate groups become one series with SD or SEM error bars; a Y column with an Error column uses those pre-computed values).
- render_qt with pyqtgraph: XY scatter/line with SD or SEM error bars and fit overlays, bar (mean with error bars), box. Legend click toggles a series on XY charts.
- Graph tab in the document; graphs are dependency-graph nodes and redraw when data or fits change.
- The main toolbar and the table toolbar from SPEC.md, following docs/DESIGN.md section 7 and docs/mockups/toolbars.html. The table toolbar's Plot button opens the chart-family menu. Draw the icons as SVG line icons coloured from the palette; pytest-qt tests that every toolbar button is a registered action and that disabled buttons say why.
- Tests for the spec builder and a benchmark for redraw after a data edit.

Make a plan first. When done, report what you built and the redraw benchmark.
```

### Step 2.6 — Launcher redesign

**Done when**
- [x] The launcher matches the approved mockup (docs/mockups/launcher.html) in the light and the dark theme.
- [x] Recent projects can be opened with one click or Enter and removed from the list with the right-click menu; a missing file shows "File not found".
- [x] Cold start to launcher stays under 1.5 s.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.6 in docs/PHASES.md.

Rebuild src/curvis/ui/launcher.py following docs/DESIGN.md section 10 and docs/mockups/launcher.html (approved: the start gallery, without logo, version or tagline):
- A row of tiles with Blank project (Ctrl+N) first; it has the focus, so Enter starts a project. Leave room for the sample tiles of step 2.9 (the tile row takes a list of tiles), but show only Blank project for now.
- A "Recent projects" heading with an Open project… button (Ctrl+O) at its right.
- The recent list as rows: name, folder (elided in the middle), relative time at the right; hovered row shaded; one click or Enter opens. A missing file is dimmed, says "File not found" and does not open. Right-click (and the menu key / Shift+F10): Open, Remove from list (Settings.remove_recent_project; the file is not touched).
- The empty state ("Projects you open or save will appear here.") with EmptyState.
- A centred column 760 px wide; narrower windows use the full width with the 12 px margin and the tiles wrap.
- Palette roles only, both themes; keyboard order tiles → Open project… → list.
- pytest-qt tests: new project from the tile and with Enter, open from the button, open a recent row by click and by Enter, a missing file does not open, Remove from list (file kept), empty state, the layout at the narrow size. Keep the cold-start benchmark passing; add the launcher to scripts/dialog_screenshots.py.

Make a plan first. When done, report what you built with before and after screenshots, the tests and the cold-start benchmark.
```

### Step 2.7 — Several tables per project, renaming in place

**Done when**
- [x] A project shows its tables as tabs along the bottom; New table (File menu, +, Ctrl+Shift+N) adds one, and tabs can be renamed and reordered.
- [x] Closing a table deletes it with its calculated columns, analyses and graphs after the warning, as one undo step; the last table cannot be closed.
- [x] The Results and Graph panels, Import data and the table toolbar follow the selected table; a project with several tables saves and opens unchanged.
- [x] Column, replicate group and category names are renamed in place in the header; Tab and Shift+Tab move to the next and previous header.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.7 in docs/PHASES.md.

Build several tables per project, following docs/DESIGN.md section 8 and docs/mockups/table-tabs.html (approved: tabs along the bottom):
- Core: RemoveTable also removes the table's analyses and graphs (and their dependency-graph nodes), and undo restores all of it, as one command with a clear change-log summary. It refuses to remove the last table. Add MoveTable (reorder); RenameTable exists. A project file without tables opens with one blank table added.
- DocumentTab: a tab bar along the bottom with one tab per table and a + button; the table view, Results panel and Graph panel show the selected table. Keep the selected table when commands change the document (undo of a removal reselects the restored table).
- Actions in ActionRegistry: New table (File menu under New project, Ctrl+Shift+N), Close table… (File menu above Close project; menu and palette only), next/previous table (Ctrl+Page Down / Ctrl+Page Up). Rename the File menu's Close to Close project. No toolbar button for New table.
- The Close table warning built from src/curvis/ui/dialogs.py, with Cancel as the default button; an empty table closes without asking. Disabled Close table says why.
- Import data goes into the selected table; the status message names it.
- Double-click rename in place and a tab context menu (Rename, Close table…, New table); drag to reorder.
- Renaming in the column header, following docs/DESIGN.md section 9 and board 6 of the mockup: double-click a column name or Rename column (no shortcut; Ctrl+Shift+R is Bulk rename; no longer a dialog) opens a field over the name; Enter or a click elsewhere keeps it, Esc cancels, Tab / Shift+Tab keep it and edit the next / previous header, stopping at the last column. Group and category bands use the same editor (Tab moves within their row). One RenameColumn (or group/category rename) per changed name; invalid names keep the field open with the reason in the status bar. Share the in-place editor with the table tab rename.
- Tests: unit tests for the commands (remove with analyses and graphs, undo, last-table refusal, move), a save/open round trip with three tables, and pytest-qt tests for the main paths (new, switch, rename, close with warning, close the last table refused, import into the selected table, panels follow the table, header rename with Enter, Esc, Tab across several headers, Shift+Tab, the last column, an invalid name, undo per name). Check the graph redraw benchmark does not regress.

Make a plan first. When done, report what you built, the tests and benchmarks, and anything open.
```

### Step 2.8 — Format dialog and export

**Done when**
- [x] The Format dialog changes everything listed in SPEC.md that applies to XY, bar and box.
- [x] PNG, SVG and PDF exports match the on-screen graph (image-diff tests pass); 300 dpi export under 1 s.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.8 in docs/PHASES.md.

Build:
- Format dialog editing the ChartSpec only (one undoable command per OK): graph title, size presets (Tall 4 × 6 cm, Wide 9 × 6 cm, Square 6 × 6 cm) and custom size, show legend, each axis (title, range, label, linear/log), each series (symbol, size, line style, width, color, error-bar type, show in legend; add, remove, reorder), color scheme and chart text size. Changes preview live. Follow docs/DESIGN.md and the Format dialog mockup approved in step 1.6.
- Graph panel toolbar (docs/mockups/graph-panel.html): Change graph type (scatter, line, bar, box), Format graph, Delete graph, Export graph.
- render_mpl: Matplotlib renderer from the same ChartSpec for XY, bar and box; export to PNG (choose dpi), SVG and PDF with embedded fonts.
- Image-diff tests comparing render_qt and render_mpl output for every variant in light and dark theme, with a documented threshold; an export benchmark.

Make a plan first. When done, report what you built, image-diff results and the export benchmark.
```

### Step 2.9 — MVP hardening and beta build

**Gate for phase 2 (MVP):** all validation tests pass, all benchmarks meet targets, and 10–20 beta users can install it.

**Done when**
- [ ] All benchmarks in CLAUDE.md that apply so far meet their targets.
- [ ] A new user can go from CSV to a finished, exported dose-response graph in under 5 minutes (you test this).
- [ ] Beta installers are built and a short getting-started guide exists.

```text
Read CLAUDE.md, docs/SPEC.md and step 2.9 in docs/PHASES.md.

Prepare the MVP beta:
- Run all tests and benchmarks; profile and fix anything that misses a target. Report before/after numbers.
- Review every dialog for defaults: the common case must work with one click on OK. List any you changed.
- Check keyboard access and the command palette for every action added so far.
- Add the three sample projects (group comparison, dose-response, XY with replicates) as tiles in the launcher, after Blank project, each with a small picture of its graph, as in docs/mockups/launcher.html. A sample opens as a new, untitled copy.
- Write docs/getting-started.md: install, import, analyze, graph, export.
- Opt-in crash reporting that sends no user data (just a local crash log with a "copy to clipboard" option is fine for now).
- Build beta installers in CI and tag v0.1.0-beta.

Make a plan first. When done, report benchmark results, changes made and anything still open.
```

---

## Phase 3 — Complete the outline

### Step 3.1 — Repeated-measures ANOVA, two-way ANOVA, interaction plot

**Done when**
- [ ] Both match R fixtures; two-way ANOVA uses category groups and runs Tukey for each significant factor.
- [ ] The interaction plot draws from two-way ANOVA results.

```text
Read CLAUDE.md, docs/SPEC.md and step 3.1 in docs/PHASES.md.

Implement:
- Repeated-measures one-way ANOVA (rows are subjects), with the existing post-hoc options.
- Two-way ANOVA with two named factors from category groups, interaction term, and Tukey post-hoc for each significant factor.
- Interaction chart family (means with error bars per factor level, lines per level of the second factor).

Order of work: R fixture scripts first (aov with Error term or afex/ez for repeated measures, car::Anova for two-way, emmeans for post-hoc), including unbalanced and missing-value cases. Show me the scripts, then generate fixtures, then implement until tests/validation passes.

Decisions to confirm with me before coding:
- Repeated measures: apply the Greenhouse-Geisser sphericity correction by default?
- Two-way ANOVA with unbalanced data: Type II or Type III sums of squares?
- How are missing values handled in repeated measures (drop the subject, or mixed model)?

Make a plan first. When done, report validation results.
```

### Step 3.2 — Kruskal-Wallis with Dunn's test

**Done when**
- [ ] Matches R fixtures, including ties; Dunn post-hoc works for all pairs and vs. control, Holm-corrected.

```text
Read CLAUDE.md, docs/SPEC.md and step 3.2 in docs/PHASES.md.

Implement Kruskal-Wallis for 3+ groups with Dunn's post-hoc test (all pairs or vs. a chosen control, Holm-corrected) using SciPy and scikit-posthocs.

Order of work: R fixtures first (kruskal.test, dunn.test or PMCMRplus::kwAllPairsDunnTest with p.adjust "holm"), with ties and unequal group sizes. Show me the scripts, generate fixtures, then implement until tests/validation passes.

Make a plan first. When done, report validation results.
```

### Step 3.3 — Remaining curve fits

**Done when**
- [ ] Biphasic dose-response, exponential association, exponential decay, Michaelis-Menten, bell-shaped and Gaussian fits match R fixtures and converge on realistic noisy data.

```text
Read CLAUDE.md, docs/SPEC.md and step 3.3 in docs/PHASES.md.

Add to the fit framework: biphasic dose-response, exponential association (one-phase), exponential decay (one-phase), Michaelis-Menten, bell-shaped dose-response, and Gaussian. Each needs robust automatic initial guesses and reports the parameters named in SPEC.md (rate constants, plateaus, Vmax, Km, peak center, width, amplitude) with SE and 95% CI.

Order of work: R fixtures first (minpack.lm::nlsLM with the same equations), with clean data, noisy data and a non-converging case per model. Write the exact equation of every model into docs/models.md and show me the scripts and equations before implementing.

Make a plan first. When done, report validation results and convergence rates on the noisy fixtures.
```

### Step 3.4 — Survival: Kaplan-Meier

**Done when**
- [ ] Time + Event column pairs are supported; Kaplan-Meier curves and 95% CIs match R.
- [ ] Survival step charts with confidence bands draw and export.

```text
Read CLAUDE.md, docs/SPEC.md and step 3.4 in docs/PHASES.md.

Implement survival analysis:
- Time + Event column pair role (event = 1, censored = 0), validated on entry.
- Kaplan-Meier estimate with 95% confidence intervals using lifelines; results table of survival at each event time, median survival with CI, numbers at risk.
- Survival chart family: step curves with censoring ticks and confidence bands; legend toggling; export.

Order of work: R fixtures first (survival::survfit), with ties, all-censored and single-event cases. Show me the scripts, then implement until tests/validation passes.

Decisions to confirm with me before coding:
- Confidence interval type: "log" (R's default) or "log-log" (lifelines' default)?

Make a plan first. When done, report validation results.
```

### Step 3.5 — Remaining charts and settings

**Done when**
- [ ] Horizontal bar, violin, histogram and grouped bar/horizontal bar/box/violin charts work and export.
- [ ] Color schemes, palette editor, custom size, and all settings in SPEC.md work.

```text
Read CLAUDE.md, docs/SPEC.md and step 3.5 in docs/PHASES.md.

Complete graphs and settings from SPEC.md:
- Chart variants: horizontal bar, violin, histogram (with bin control), and grouped bar, horizontal bar, box and violin for category groups, in both renderers, with image-diff tests.
- Color schemes for charts and a palette editor; default palette for new columns and series.
- Settings: default chart aspect ratio, chart text size, default palette (theme and decimal places exist already).

Make a plan first. When done, report what you built and image-diff results.
```

### Step 3.6 — Excel and Prism import

**Gate for phase 3:** every item in SPEC.md works; check it line by line.

**Done when**
- [ ] .xlsx and .xls import through the same preview dialog (sheet picker added).
- [ ] Data tables from Prism .pzfx and .prism files import with sensible column roles and groups.

```text
Read CLAUDE.md, docs/SPEC.md and step 3.6 in docs/PHASES.md.

Extend import:
- Excel: .xlsx via fastexcel, .xls via xlrd, through the existing preview dialog with an added sheet picker.
- Prism .pzfx: data tables are XML at the start of the file. Map Prism table types (XY, column, grouped, survival) and subcolumns (replicates, mean/SD/N) to Curvis roles, replicate groups and category groups. Use the open-source R package pzfx as a reference for the structure; do not copy its code.
- Prism .prism: data is stored as CSV inside the file with JSON metadata. Import data tables the same way.
- Analyses and graph styling are not imported; show a short note saying so.
- Tests with real Prism files I will put in tests/data/prism (ask me for them before you start). Never include Prism files you did not receive from me.

Make a plan first. When done, report which Prism table types import and any that do not.
```

---

## Phase 4 — Release 1.0

### Step 4.1 — Robustness, accessibility and documentation

**Done when**
- [ ] Autosave and crash recovery work; old-version project files open.
- [ ] Screen-reader labels and full keyboard access are in place.
- [ ] User guide covers every feature.

```text
Read CLAUDE.md, docs/SPEC.md and step 4.1 in docs/PHASES.md.

Prepare for 1.0:
- Autosave a recovery copy every 3 minutes; offer recovery on next start after a crash.
- Migration tests: projects saved by every previous schema version open correctly.
- Accessibility: accessible names for all controls, full keyboard operation, sufficient contrast in both themes.
- Final performance pass against every target in CLAUDE.md.
- docs/user-guide/: one page per feature area in SPEC.md, with screenshots generated by a script.

Make a plan first. When done, report results for each item.
```

### Step 4.2 — Validation report and 1.0 release

**Gate for phase 4:** you read the validation report and approve the release.

**Done when**
- [ ] A generated validation report lists every analysis, its R reference, versions and measured agreement.
- [ ] Signed installers for Windows and macOS and an AppImage for Linux are attached to the v1.0.0 release.

```text
Read CLAUDE.md, docs/SPEC.md and step 4.2 in docs/PHASES.md.

Release 1.0:
- A script that runs tests/validation and writes docs/validation-report.md (and a PDF): each analysis, the R function and package versions used, number of fixtures, the largest difference found per output, and the approved exceptions from TOLERANCES.md.
- Code signing in CI: Windows signing and macOS signing plus notarization using secrets I will add (tell me exactly which secrets are needed). Linux AppImage.
- Changelog, release notes and version bump to 1.0.0; a GitHub Release with all installers.

Make a plan first. Do not publish the release until I approve it. When done, report what is ready.
```

---

## Prompt for anything not covered above

Use this for bug fixes and small changes between steps.

```text
Read CLAUDE.md and docs/SPEC.md.

Task: <describe the bug or change>

Follow the architecture and validation rules in CLAUDE.md. If this touches statistics, add or update the R fixture first and show it to me. Make a plan first, add a test that fails before the fix and passes after, and report the result.
```
