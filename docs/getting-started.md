# Getting started with Curvis

Curvis analyzes and plots scientific data: you put your measurements in a table, run a
statistical test or a curve fit, and export a graph for a paper or slides. This guide
takes you from installing Curvis to an exported dose-response graph in about five
minutes.

This is a **beta** (version 0.1.0b1). The core analyses are checked against R, but some
features in the plan are not there yet: Excel import, two-way ANOVA, Kruskal-Wallis,
survival curves and some fits. Please tell us what works and what does not.

## 1. Install

Download the installer for your computer from the
[v0.1.0-beta release](https://github.com/curvis-labs/curvis/releases/tag/v0.1.0-beta).
The installers are not signed yet, so Windows and macOS ask before the first start.

| Computer | File | How to install |
| --- | --- | --- |
| Windows 10 or 11 | `Curvis-0.1.0b1-setup.exe` | Run it. If Windows says "Windows protected your PC", click **More info**, then **Run anyway**. No administrator rights are needed. |
| Mac with Apple silicon (M1 or later), macOS 12 or later | `Curvis-0.1.0b1-macos-arm64.dmg` | Open it and drag **Curvis** to **Applications**. The first time, right-click Curvis in Applications and choose **Open**, then **Open** again (or allow it under **System Settings › Privacy & Security**). |
| Linux: Debian, Ubuntu | `curvis_0.1.0b1_amd64.deb` | `sudo apt install ./curvis_0.1.0b1_amd64.deb`, then start Curvis from the app menu or with `curvis`. |
| Linux: other | `Curvis-0.1.0b1-x86_64.AppImage` | `chmod +x Curvis-0.1.0b1-x86_64.AppImage`, then run it. |

The installer for Windows, the Mac app and the `.deb` let you open `.cvp` project files
with a double-click.

## 2. Start

Curvis opens on the start screen. Click a tile to begin:

- **Blank project**: an empty table (also Ctrl+N).
- **Group comparison**, **Dose-response** and **XY with replicates**: sample projects
  with data, an analysis and a graph. Each opens as a new copy, so you can change it
  freely; **Save** asks where to keep it. The samples are also under **File › New from
  sample**.

Recent projects are listed under the tiles; click one to open it.

Every command is in the menus, and **Ctrl+K** opens the command palette: type a few
letters of what you want to do ("export", "group") and press Enter. On a Mac, use Cmd
where this guide says Ctrl.

## 3. Import your data

Curvis reads CSV, TSV and text files, such as the export of a plate reader. Lay the
file out with one column per measurement: for a dose-response experiment, the
concentrations in the first column and one column per replicate.

```text
Concentration (µM),Rep 1,Rep 2,Rep 3
0.01,3.2,2.4,5.6
0.1,8.0,5.9,10.5
1,40.1,33.2,48.9
...
```

1. Choose **File › Import data…** (Ctrl+Shift+I; once a project is open, also **Import
   data** on the toolbar) and pick the file. Without an open project, Curvis makes a new
   one; otherwise the data go into the table you are looking at.
2. The Import dialog shows a preview. Curvis detects the delimiter (comma, semicolon,
   tab or space) and takes the first row as the column names; change **Delimiter** or
   **Header row** if the preview looks wrong. For files that use a decimal comma
   (`1,5`), open **Advanced**.
3. Tick **First column is X** when the first column holds concentrations, times or
   another X variable.
4. Click **Import**.

Each column has a **role**, shown in its header: **X** for the independent variable,
**Y** for measurements, **Error** for error values you computed yourself, and **Text**.
To change a role, select the column and use **Table › Column role**.

You can also type or paste (Ctrl+V) values straight into the table. Double-click a
column name to rename it; Tab moves to the next name.

### Group replicates

Replicate columns of the same condition belong together, so Curvis can compute their
mean, SD and SEM. Select the three replicate columns (click the first header, Shift-click
the last) and click **Group as replicates** on the toolbar (Ctrl+G). Click **OK** to
accept the suggested name, or type your own, such as the name of the compound.

## 4. Analyze

Click **Analyze** on the toolbar (Ctrl+Shift+A). The Analyze dialog only shows the
analyses your table supports, and opens on the family that fits it: **Fit a curve** for
a table with an X column, **Compare groups** otherwise.

- **Dose-response:** choose **Hill 4PL (variable slope)**. If your X values are already
  log10 of the concentration, tick **X values are log10(concentration)**. Click **Run**.
- **Compare groups:** for a table with one column (or replicate group) per group,
  choose **t test** for two groups or **One-way ANOVA** for three or more, with a
  post-hoc test (Tukey, Dunnett against a control, or Holm). Click **Run**.

The **Results** panel next to the table shows the result: the method by name, a one-line
reading in plain words, and the numbers (for a 4PL fit: EC50 with its 95% confidence
interval, Hill slope, top, bottom and R²). Significance stars mean * P < 0.05,
** P < 0.01 and *** P < 0.001. Warnings appear when an assumption looks violated or a
fit did not converge. Copy the values with Ctrl+C to paste them elsewhere.

**Edit…** in the Results panel reopens the analysis with its settings, to change and
run it again. When you change the data, the analysis updates by itself.

## 5. Graph

Click **Plot** on the toolbar and choose a chart family:

- **XY** for a dose-response or time course. Replicate groups are drawn as their mean
  with error bars, and curve fits are drawn over the points. A dose-response fit on
  concentrations gets a log X axis.
- **Bar** for comparing groups (bar, box and more under **Change graph type**).

The graph opens in the **Graph** panel next to the table, and redraws when the data
change. On an XY graph, click an entry in the legend to hide or show that series.

**Format graph** (on the Graph panel's toolbar, or Ctrl+Shift+F) sets the title, size
(Tall 4 × 6 cm, Wide 9 × 6 cm, Square 6 × 6 cm or your own), legend, axis titles,
ranges and log scales, each series' symbol, line, color and error bars, the color scheme
and the text size. The graph previews your changes; click **Apply** to keep them.

## 6. Export

Click **Export graph** on the Graph panel's toolbar (Ctrl+Shift+X) and choose a format:

- **PNG** for slides and documents, at the resolution you choose (300 dpi by default).
- **PDF** for print and journals, with its font embedded.
- **SVG** to edit the drawing in a vector program.

Click **Export…** and choose where to save the file. The export has the graph's size,
as set in Format graph.

## 7. Save

**File › Save** (Ctrl+S) saves the whole project, with its tables, analyses and graphs,
in one `.cvp` file. **Edit › Undo** (Ctrl+Z) and **Edit › Redo** go back and forth
through your last 50 changes.

## If Curvis runs into a problem

You can turn on a crash log in **Edit › Settings…** (Ctrl+,; on a Mac, **Curvis ›
Settings…**): tick **Keep a crash log on this computer**. It is off until you turn it on, and **nothing is ever sent**. When
Curvis runs into a problem, it saves a short report on your computer and shows it, with a
**Copy to clipboard** button. The report has the versions of Curvis, Python, Qt and your
operating system, and where in Curvis the problem happened; your home folder is written
as `~`, and it contains none of your data. Paste it into an email or a
[GitHub issue](https://github.com/curvis-labs/curvis/issues) to help us fix it.
