# Changelog

Curvis follows [semantic versioning](https://semver.org/). A project saved by one
version opens in every later version.

## 1.0.0 — unreleased

The first stable release. Every analysis is checked against reference results from R
and, for curve fits, the NIST Statistical Reference Datasets; the
[validation report](docs/validation-report.md) lists each one with its R reference and
the largest difference found. The Windows installer is signed, and the macOS app is
signed and notarized by Apple, so neither warns before the first start any more.

### New since the 0.1.0 beta

- **Analyses.** Repeated-measures one-way ANOVA (Greenhouse-Geisser correction) and
  two-way ANOVA with Tukey's test for each significant factor; Kruskal-Wallis with
  Dunn's test (all pairs or against a control, Holm-corrected); biphasic and
  bell-shaped dose-response, exponential association and decay, Michaelis-Menten and
  Gaussian fits; Kaplan-Meier survival curves with 95% confidence intervals.
- **Graphs.** Horizontal bar, violin, column scatter and histogram graphs; grouped
  graphs for category groups; survival and interaction plots. Comparisons of a t test,
  Mann-Whitney test, ANOVA post-hoc test or Dunn's test can be drawn on bar-family graphs
  as brackets with stars or P values. Graphs can be renamed. A choice of bundled chart
  fonts makes a graph look the same on every computer.
- **Data.** Excel (.xlsx, .xls) import, and import of the data tables of GraphPad Prism
  files (.pzfx, .prism). Columns can be put in a category directly; new tables start
  with empty Y columns.
- **Analyze dialog.** One list per family with recently used analyses on top and a
  filter field.
- **Autosave and recovery.** Unsaved projects are written as recovery copies every few
  minutes, and offered back after a crash.
- **Settings.** Default palette (with an editor), graph size, chart font and text size,
  decimal places, decimal mark for import, how P values are shown, export defaults and
  the autosave interval.
- **Accessibility.** Every action is reachable by keyboard and from the command palette
  (Ctrl+K); screen-reader names on the main controls.
- **Documentation.** A user guide (docs/user-guide) next to the getting-started guide.

### Changed

- Projects use file format 11. Projects from the beta open unchanged and are saved in
  the new format.

## 0.1.0-beta

The MVP beta: data tables with replicate and category groups, CSV import, formulas and
calculated columns; descriptive statistics, t tests, one-way ANOVA with Dunnett, Holm
and Tukey, Mann-Whitney; linear, polynomial, 4PL and 3PL fits; XY, bar and box graphs
with the Format dialog and PNG, SVG and PDF export. Unsigned installers for Windows,
macOS and Linux.
