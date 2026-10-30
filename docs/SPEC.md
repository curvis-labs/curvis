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
- **Bulk rename** relabels many columns and groups in one step.

Columns and column groups are movable by drag and drop.

**Import.** CSV, TSV and Excel (.xlsx and .xls) files load through a preview dialog where you pick the header row and the delimiter (auto-detect, comma, semicolon, tab or space), and optionally the range of rows and columns to import.

**Calculated columns.** The Calculate dialog adds derived columns from four ready-made calculations or a custom formula:

- Baseline subtraction
- Normalize to 0–100%
- Fold change relative to a reference value
- Custom formula

Formulas use `[column]` references, the operators + − × ÷ ^ and %, and the column functions FIRST, LAST, ROW(column, n) (the value in row n of a column), MIN, MAX, MEAN, SUM and COUNT. They are parsed safely. Calculated columns can be edited after they are created.

**Constants.** Formulas can use built-in physical constants (Avogadro, gas constant, Boltzmann, Planck, speed of light, elementary charge, Faraday, π and e). They can also use your own constants, saved either in the project or globally on your computer.

## Analyses

Curvis has 18 built-in analyses in three families. The Analyze dialog shows only the analyses your table can support. It opens on the family that best fits the data: survival when there are time/event pairs, curve fits when there are XY pairs, and group comparisons otherwise.

| Family | Analysis | Details |
| --- | --- | --- |
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

Graphs are drawn directly from a data table. You pick a chart family from the toolbar, and the graph redraws when the data changes.

| Chart family | Variants |
| --- | --- |
| XY | Scatter and line plots with SD or SEM error bars, and fitted curves overlaid |
| Bar | Bar, horizontal bar, box, violin and histogram; grouped bar, horizontal bar, box and violin for category groups |
| Survival | Kaplan-Meier step curves with confidence bands |
| Interaction | Plot of two-way ANOVA results |

The Format dialog controls:

- Graph title and aspect ratio (4:3, 16:9, 1:1, 3:4, 3:2 or custom size)
- Each axis: title, range, label, and linear or log scale
- Each series: symbol, symbol size, line style, line width, color and error-bar type
- Color scheme and chart text size

Clicking a legend entry on XY and survival charts toggles that series on or off. Graphs export as PNG, SVG or PDF.

## Projects, history and settings

- **Projects.** All of a project's tables, graphs and analyses, plus its own constants, are saved together in one `.cvp` file. Each document opens as a tab in the main window.
- **Launcher.** On start-up, a launcher screen offers a blank project, an Open button and a list of recent projects with relative timestamps (e.g. "2 days ago").
- **Undo and redo.** Each document keeps up to 50 undo steps.
- **Settings.** Theme (light, dark or follow the system), default color palette for new columns and series (with a palette editor), default chart aspect ratio, chart text size, and the number of decimal places shown in calculated columns.
