# Curvis — Software Description

Oct 29, 2026 · @Max

## Overview

Curvis is a desktop application for analyzing and plotting scientific data. You enter or import measurements into data tables, then run statistical tests and curve fits and draw publication-ready graphs, all from one project window.

It is aimed at lab scientists who need common bench statistics, such as dose-response fits, enzyme kinetics, group comparisons and survival curves, without writing code.

It runs on Windows, macOS and Linux and has light and dark themes.

## Workflow

Data is imported or typed into a table, and calculated columns are written back into the same table. Analyses and graphs both read from that table, and curve fits can be overlaid on XY graphs.

## Data tables and formulas

Data lives in spreadsheet-style tables where every column has a role. The role tells Curvis how to analyze and plot that column.

| Column role | Used for |
| --- | --- |
| X | Independent variable, e.g. concentration or time |
| Y | Measured values |
| Error | Pre-computed error values for a Y column |
| Time + Event pair | Survival data: follow-up time plus an event or censoring flag |

Columns can be organized further:

- **Replicate groups** combine several Y columns into one series, from which mean, SD and SEM are calculated.
- **Category groups** define a second factor for two-way ANOVA and grouped charts.
- **Renaming in place.** Double-clicking a column name in the header edits it in place; Tab keeps the name and moves to the next column header on the right (Shift+Tab to the left), so a row of names can be typed in one go. Replicate group and category names in the header edit the same way.
- **Bulk rename** relabels many columns and groups in one step.
- **Columns in a category.** A Y column put in a category directly (not through a replicate group) is a series of one column, with its values down the rows. The header shows its name, e.g. "Control", under the category's name. Its *full name* is "category: name" ("Male: Control"), so "Control" can be a column in every category. Full names are unique in the table (a column outside every category has its plain name as its full name). Formulas, copied and exported headers use the full name; a formula may use the short name where only one column has it. Renaming a category renames its columns' full names, and formulas follow. Taking a column out of its category makes its full name its name ("Male: Control"); putting a column into a category whose name begins its name drops that beginning ("Male: Control" put in Male is "Control"). A column cannot move into a category that already has a series of its name; Curvis says so. Undo restores the names exactly.
- **Empty columns.** A new table has an X column and three empty Y columns, A, B and C. They are ordinary columns: values are typed or pasted into them, and they can be renamed, given another role or deleted. While a column has no values, analyses do not tick it and new graphs leave it out. More columns are added with *Add column*; a paste that runs past the last column adds Y columns for the rest of it.

Columns and column groups are movable by drag and drop.

**Import.** CSV, TSV and Excel (.xlsx and .xls) files load through a preview dialog where you pick the header row, the delimiter (auto-detect, comma, semicolon, tab or space) and the decimal mark (auto-detect, point or comma; the default comes from Settings), and optionally the range of rows and columns to import.

**Calculated columns.** The Calculate dialog adds derived columns from four ready-made calculations or a custom formula:

- Baseline subtraction
- Normalize to 0–100%
- Fold change relative to a reference value
- Custom formula

Formulas use `[column]` references, the operators + − × ÷ ^ and %, and the column functions FIRST, LAST, ROW(column, n) (the value in row n of a column), MIN, MAX, MEAN, SUM and COUNT. They are parsed safely. Calculated columns can be edited after they are created.

One calculation can be applied to many columns at once: it makes one calculated column per selected column, named with a suffix. The selected columns can be **paired** with two roles, A and B (the names can be changed, e.g. "Control" and "Treated"). Columns are paired by position: the first B column with the first A column, and so on (index-matched replicates); a single A column goes with every B column. The same pairing is used later by paired analyses. Formulas refer to roles with `{A}` (the matching column), `{B}` (the column being calculated) and `{A:mean}` (the row-wise mean of all columns of the role, ignoring missing values). Baseline subtraction, fold change and custom formulas can use pairing; normalizing works on each column by itself, to its own minimum and maximum or to typed 0% and 100% values.

**Constants.** Formulas can use built-in physical constants (Avogadro, gas constant, Boltzmann, Planck, speed of light, elementary charge, Faraday, π and e). They can also use your own constants, saved either in the project or globally on your computer.

## Analyses

Curvis has 19 built-in analyses in three families. The Analyze dialog shows only the analyses your table can support. It opens on the family that best fits the data: survival when there are time/event pairs, curve fits when there are XY pairs, and group comparisons otherwise.

In the dialog the analyses of a family are one list, grouped in sections (for curve fits: linear, dose-response, kinetics and shape-based), with recently used ones on top and a filter field to find one by name or by what it reports. The options sit under the data: a fit's X range is two number fields, and parameters are fixed in a table (a *Fix* check box and a value per parameter), so nothing has to be typed in a syntax. A panel, *About this analysis*, explains the chosen analysis before it runs: for a fit, its equation and a sketch of its curve with the parameters marked (a fixed parameter shown at its value); for a test, its null hypothesis and assumptions; for every analysis, one sentence on what will run and when to use it.

| Family | Analysis | Details |
| --- | --- | --- |
| Compare groups | Descriptive statistics | n, mean, SD, SEM and 95% confidence interval of each replicate group, row by row |
| Compare groups | t-test | Unpaired or paired; mean difference with a confidence interval |
| Compare groups | One-way ANOVA | Ordinary or repeated measures; Dunnett (vs. control), Holm or Tukey post-hoc tests |
| Compare groups | Two-way ANOVA | Two named factors, built from category groups; Tukey post-hoc test for each significant factor |
| Compare groups | Mann-Whitney U | Non-parametric test for two groups |
| Compare groups | Kruskal-Wallis | Non-parametric test for 3+ groups; Dunn post-hoc test (all pairs or vs. control, Holm-corrected) |
| Fit a curve: linear | Linear regression; polynomial of degree 2, 3 or a custom degree (2–6) | Slope, intercept, R² and confidence bands |
| Fit a curve: sigmoidal / dose-response | Hill 4PL (Hill slope sign is free), 3PL (bottom fixed at 0), biphasic | EC50, Hill slope, top and bottom |
| Fit a curve: kinetics | Exponential association, exponential decay, Michaelis-Menten | Rate constants, plateaus, Vmax and Km |
| Fit a curve: shape-based | Bell-shaped, Gaussian | Peak center, width and amplitude |
| Survival | Kaplan-Meier | Survival curves with 95% confidence intervals |

Curve fits use lmfit (least-squares fitting with parameter constraints) and report each parameter with its standard error and confidence interval. A fit can be limited to an X range, and several series can be fitted in one run.

Results appear in a Results panel next to the data, with significance stars (\*, \*\*, \*\*\*). The values can be copied with Ctrl+C. An analysis keeps a link to its source table, so you can change its parameters and run it again later. Recently used analyses are remembered.

## Graphs

Graphs are drawn directly from a data table. You pick a chart family from the toolbar, and the graph redraws when the data changes. The graph appears in a Graph panel next to the data. The user can toggle between Result and Graph panel.

| Chart family | Variants |
| --- | --- |
| XY | Scatter and line plots with SD or SEM error bars, and fitted curves overlaid |
| Bar | Bar, horizontal bar, box, violin (trimmed at the data range or not), scatter and histogram (automatic bins, a number of bins, or a bin width and start); grouped bar, horizontal bar, box and violin for category groups, clustered by replicate group (colored by category) or by category (colored by replicate group); option to show individual values with symbols |
| Survival | Kaplan-Meier step curves with confidence bands |
| Interaction | Plot of two-way ANOVA results |

The Format dialog controls:

- Graph title, graph type (the types of its family: XY and Bar graphs can change between theirs; survival and interaction graphs keep theirs), size (Tall 4 × 6 cm, Wide 9 × 6 cm, Square 6 × 6 cm, or a custom width and height) and whether the graph shows a legend
- Each axis: title, range, label, linear or log scale, and the angle of the X tick labels (Auto, horizontal, 45° or 90°; Auto turns them when they do not fit side by side)
- Each series: symbol, symbol size, line style, line width, color, error-bar type and whether it is listed in the legend; series can be added, removed and reordered
- Fitted curves laid over the graph, listed with the series; they can be removed
- Color scheme, chart font (DejaVu Sans, Liberation Sans, Liberation Serif or Inter, bundled with Curvis so a graph looks the same on screen, in exports and on every computer) and chart text size

The graph's page leaves room for everything it shows: tick labels that reach past the plot get a margin, turned labels too long for the page are shortened with "…", and a legend too wide for the right of the plot goes under it.

The Graph panel has a small toolbar to change the graph type (scatter, line, bar or box), format, delete and export the graph shown.

Deleting a graph or an analysis asks first (the Results panel has *Delete…* next to *Edit…*) and can be undone. Deleting a curve fit also removes its curves from the graphs that show them.

Clicking a legend entry on XY and survival charts toggles that series on or off. Graphs export as PNG (at a chosen resolution), SVG or PDF, at the graph's size.

## Projects, history and settings

- **Projects.** All of a project's tables, graphs and analyses, plus its own constants, are saved together in one `.cvp` file. Each document opens as a tab in the main window.
- **Tables in a project.** A project holds one or more tables, shown as tabs along the bottom of the project's tab, like the sheets of a spreadsheet. The Results and Graph panels show the analyses and graphs of the selected table, and Import data and the table toolbar act on it. *New table* (File menu, the + after the last table tab, Ctrl+Shift+N) adds a blank table after the selected one. Table tabs can be renamed (double-click) and reordered (drag). Closing a table deletes it, with its calculated columns, analyses and graphs, after a warning that lists them; Undo brings it back. A project always keeps at least one table, so the last table cannot be closed.
- **Toolbars.** A main toolbar with New project, Open project, Save project, Undo and Redo, and a table toolbar with Import data, Add column, Group as replicates, Set category, Calculate, Plot and Analyze. Plot opens the chart families to pick from. Every toolbar button is also in the menus and the command palette.
- **Launcher.** On start-up, and whenever no project is open, a launcher screen shows a row of tiles to start from (a blank project and the sample projects, each sample with a small picture of its graph), an Open button, and a list of recent projects with their folder and a relative timestamp (e.g. "2 days ago"). A recent project opens with one click; right-clicking it offers to remove it from the list (the file is kept). A missing file is shown as "File not found".
- **Undo and redo.** Each document keeps up to 50 undo steps.
- **Autosave and recovery.** Every few minutes (3 by default, set in Settings), each open project with unsaved changes, including one never saved, is written as a recovery copy to the app's own data folder. A recovery copy never replaces the project file and is deleted when the project is saved, closed or its changes discarded. If Curvis did not close normally, the launcher shows the recovered projects with their name and the time of the copy; each can be opened or discarded, and copies are kept until then. A recovered project opens with unsaved changes, and Save writes to its original file, after a warning if that file was changed since the copy was made. Its undo history starts empty; its change log is kept.
- **Settings.**
  - Appearance: theme (light, dark or follow the system).
  - New graphs: default color palette for new columns and series (with a palette editor), default graph size (one of the Format dialog's sizes or a custom width and height), chart font and chart text size.
  - Tables: the number of decimal places shown in calculated columns, and the default decimal mark for import (point or comma).
  - Results: how P values are shown: "P < 0.001" (the default), "P < 0.0001", or exact (e.g. 2.3 × 10⁻⁷). This changes only the display; stored results keep full precision, and the significance stars do not change.
  - Export: default file format and PNG resolution, filled in by the Export dialog.
  - Projects: how often to autosave a recovery copy (1–30 minutes, default 3; autosave is always on), and *Clear recent projects*, which asks first and keeps the files.
  - Privacy: keep a local crash log (off by default; nothing is sent).
  - *Reset to defaults* asks first and restores every setting above; recent projects, global constants and recovery copies are kept.

  Settings only give starting values. A new graph copies the palette, size, font and text size into its own definition, so changing a setting later does not change existing graphs, and a project looks the same on every computer. Only the theme and the display of numbers and P values follow the current settings.

## Planned after 1.0

Not part of 1.0; do not build. Listed so that 1.0 does not make it harder.

- **User-defined curve fits.** The user types a model as a formula (in the same formula language, never evaluated as Python), names its parameters and gives starting values; it appears in the Analyze dialog under its own section, with a sketch drawn from the formula and the same parameter table as the built-in fits (later with bounds). Validation comes from the fit engine (NIST StRD and fixtures for general formulas), as a user's model has no R reference of its own.
