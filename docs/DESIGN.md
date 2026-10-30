# Curvis — UI design guidelines

Short rules for dialogs and panels. The shared code for them is `src/curvis/ui/dialogs.py`;
use it instead of building margins, button rows or collapsible sections by hand.
Mockups for dialogs still to be built are in `docs/mockups/`.

## 1. Dialog layout

### The workbench dialog (reference: Calculate)

Use it when the user picks **one of several kinds** of thing *and* chooses **inputs** *and*
sets **options** whose **result** they want to see before clicking OK (Calculate, Analyze,
Format, Import).

```
┌ Title ───────────────────────────────────────────────────────────────┐
│ [Kind 1] [Kind 2] [Kind 3] [Kind 4]                ← choices as tabs  │
│ One sentence on what the selected kind does.       ← secondary text   │
│ ┌ Inputs ──────────────┐ ┌ Options ───────────────────────────────┐ │
│ │ what to work on      │ │ options of the selected kind (stacked) │ │
│ │ (columns, file …)    │ │ ▸ Advanced         ← collapsed section │ │
│ │                      │ ├ Result ────────────────────────────────┤ │
│ │                      │ │ formula, preview, live graph …         │ │
│ └──────────────────────┘ └────────────────────────────────────────┘ │
│ ⚠ Feedback line: why OK is disabled, or a warning                    │
│ Secondary settings (suffix, placement)          [Calculate] [Cancel] │
└──────────────────────────────────────────────────────────────────────┘
```

- **Choices** are a tab bar across the top, left-aligned, not stretched. The first tab is the
  most used kind, or the one that best fits the data. A one-line description of the selected
  kind sits under the tabs.
- **Inputs** go in the left column; **options** and **result** go in the right column. Both
  columns get equal stretch, unless one holds a wide list or preview, which then gets 3 : 2
  (Import's preview, Bulk rename's list of names). Options sit at the top of the right column at the height of their
  current page. The result takes the rest of the height. When a kind has nothing to pick
  (Format › Graph), its options move into the left column and the result stays on the right.
- **Secondary settings** (naming, placement, “append at the end”) go at the left of the button
  row. They never go in the main columns.
- **Advanced options** go in a collapsed `Advanced` section at the bottom of the options panel
  they extend (in Import, the options on the left). It is closed by default and stays closed
  every time the dialog opens. The common case never needs it. Use
  `CollapsibleSection`.

### The simple dialog

Use one column (a form, then the feedback line, then the buttons) when there is **no kind to
choose and no result to preview**, and at most about eight fields: Settings, the constant
editor, Export. A **list manager** (Constants) is one column too: scope tabs on top, a list,
then a row of *Add… / Edit… / Delete* buttons under the list, and *Close*.

Do not use group boxes in a simple dialog. A form needs no frame.

## 2. Spacing and sizes

All values are in device-independent pixels and live in `curvis.ui.dialogs` as constants.

| Constant | Value | Use |
| --- | --- | --- |
| `MARGIN` | 12 | Dialog content margin, all sides |
| `SPACING` | 8 | Between related controls; form rows; inside a group box |
| `SECTION_SPACING` | 16 | Between the two columns, and between sections stacked vertically |
| `INDENT` | 20 | Fields that belong to the radio button or check box above them |
| `FIELD_WIDTH` | 120 | Short number and text fields (values, suffixes) |

Inner layouts (pages, rows) have zero margins; only the dialog has a margin.

| Dialog | Default size | Minimum size |
| --- | --- | --- |
| Workbench (Calculate, Analyze, Format, Import) | 900 × 640 | 820 × 540 (to be confirmed for Analyze and Format in steps 2.2 and 2.6) |
| Bulk rename | 720 × 520 | 600 × 420 |
| Constants | 560 × 420 | 460 × 340 |
| Simple dialog (Settings, constant editor) | its size hint | its size hint, at least 360 wide |

At the default and the minimum size **nothing overlaps and nothing is cut off**. The tests
in `tests/ui/test_dialog_design.py` check this for every dialog.

**Resizing.** Extra width is shared equally between the two columns. Extra height goes to the
one widget that holds a list, a table, a preview or a graph (stretch 1). Everything else keeps
its preferred height. A **stacked panel takes the height of the page shown, not the height of
its tallest page**: use `FittedStack`. Long labels word-wrap; they never set the dialog's width.

## 3. Colour

The themes use the colours of the app icon: navy #1D3557, teal #00A6A6, emerald #20C997,
slate #667085 and off-white #F4F6F8. Use **palette roles only**, never hex values in widget
code. `curvis.ui.theme` sets every role in both themes. Each text/background pair reaches
at least **4.5 : 1**, and `tests/ui/test_theme.py` checks it.

| Use | Role | Light | Dark |
| --- | --- | --- | --- |
| Dialog and window background | `Window` | #F4F6F8 off-white | #152840 deep navy |
| Fields, lists, tables | `Base` | #FFFFFF | #0F1F33 |
| Normal text | `WindowText` / `Text` | #1D3557 navy | #F4F6F8 off-white |
| Secondary text: kind descriptions, hints, dimmed preview rows, empty states | `PlaceholderText` | #667085 slate | #9AA8BC light slate |
| Selection | `Highlight` / `HighlightedText` | #007A7A dark teal / white | #00A6A6 teal / #0F1F33 |
| References in formulas (`[column]`, `{A}`) | `Link` | #007A7A | #00A6A6 |
| Calculated-column background | `AlternateBase` | #E0F2F1 teal tint | #1C3A4A teal tint |
| Accent: fills only, used sparingly (focus, “done”) | `Accent` | #20C997 emerald | #20C997 |
| **Errors**: the error line, an invalid field's message | `BrightText` | #B3261E | #FF8080 |
| Outlines of fields, lists, check boxes, radio buttons | `control_border()`: `Text` mixed 60 % into `Base`, ≥ 3 : 1 | #77869A | #98A0A9 |
| A checked toggle button (*Not paired* / *Paired*) | `Highlight`, filled at 25 % | | |
| Table grid lines | `grid_line()`: `Text` mixed 22 % into `Base`, ≥ 1.3 : 1 on `Base` and `AlternateBase` | | |
| Tab close button | a thin × in `PlaceholderText`; `Text` on a soft square when hovered | | |

- The icon's teal is too light for text on white (3.0 : 1), so the light theme uses a darker
  teal. Emerald is never used for text in the light theme.
- Red is the only colour not taken from the icon; nothing in the icon can say “error”.
- Fusion draws outlines a shade darker than the window, which vanishes on a dark window, so
  `CurvisStyle` (in `theme.py`) draws them with `control_border()` instead, the table grid
  with `grid_line()`, and the tab close button as a thin cross instead of Fusion's bitmap.
- **Warnings** are not coloured. They show the standard warning icon in front of normal text.
  **Errors** use the error icon and `BrightText`. Colour is never the only signal, because
  icons and words carry the meaning too.
- **Role badges** (X, Y, Error, Time, Event in the column header) are text on the header's
  second line, in the header's own text colour. A calculated column adds its mark (ƒ) and,
  when its calculation fails, the error mark (⚠). They do not use colour.
- Do not dim text by disabling the label. A disabled label means “not available”. Secondary
  text uses `PlaceholderText` through `secondary_label()`.
- **Chart colours are not theme colours.** Series colours come from the chart's colour scheme
  (step 3.5), so an exported graph looks the same whatever theme the app uses.

## 4. Wording

- **Sentence case** everywhere: titles, tabs, labels, buttons (“Bulk rename”, “Column suffix”).
- **Window titles** name the task: “Calculate”, “Edit calculation”, “Import data”, “Bulk rename”,
  “Constants”, “Settings”.
- **The OK button is a verb for what it does**: *Calculate*, *Update* (when editing), *Import*,
  *Rename*, *Run* (Analyze), *Apply* (Format), *Save* (Settings), *Add* / *Save* (constant
  editor). Cancel stays *Cancel*. A dialog whose changes take effect immediately has only *Close*.
- **Button order follows the platform**, as `QDialogButtonBox` does (OK–Cancel on Windows and
  KDE, Cancel–OK on macOS and GNOME). Never order the buttons by hand.
- A button or menu item that opens another dialog ends in “…” (*Manage constants…*, *Add…*).
- Form labels have **no trailing colon**. A radio button or check box that leads into a field on
  the same line may end in one (“Subtract a value:”).
- **Mnemonics** (`&`): every label of a field, every check box, radio button and push button
  has one, except the dialog's own buttons (OK, Cancel, Close have Enter and Esc). Widgets
  visible at the same time must not share a letter. Pick the first letter if free, then a
  strong consonant. Choice tabs need none (Ctrl+Tab moves between them), and in a crowded
  dialog a button that acts on a selection may go without one when no letter is free
  (Calculate's *Set role A* / *Set role B*). A group box may carry one; it moves the focus to
  its first field (Calculate's *Fo&rmula*). Connecting words (“to” in *Rows 2 to Last*) are
  plain text; the fields after them have an accessible name. The tests fail on a clash or a
  missing mnemonic.
- Messages are one short sentence that says **what to do**: “Enter a column suffix.”, not
  “Invalid suffix”.

## 5. Feedback

- **The feedback line** sits just above the button row and spans the dialog. It shows, in order of
  priority: an error (icon + `BrightText`), a warning (icon), or a status sentence (“3 names will
  change.”). When there is nothing to say it is empty but keeps its height, so the layout does
  not jump.
- **A disabled OK always shows why.** The reason is either in the feedback line or directly under
  the one field that causes it (like the formula error under the formula). It is never in both
  places and never missing. `DialogSkeleton.set_problem()` does both: it sets the text and
  enables or disables OK.
- **Field errors** go directly under the field and mark the place (a wavy underline in a formula).
- **Work in progress** (reading a file, running an analysis) shows a status sentence (“Reading the
  file…”). OK stays disabled until there is a result.
- **Results panel**: each result starts with its plain-language reading (one sentence, normal
  text). Assumption warnings follow, each with the warning icon. Then come the numbers. An
  analysis that cannot run shows the error and what to change instead of numbers. A stale result
  stays visible with a “Recalculating…” badge.
- **Empty states**: an empty list or table shows one centred `PlaceholderText` sentence that says
  what belongs there and how to add it (“No global constants yet. Click Add to make one.”).
  It never shows just a blank box. Use `EmptyState`.

## 6. Keyboard

- **Tab order is reading order**: choice tabs → left column (top to bottom) → right column
  (top to bottom, Advanced at the end of its options panel) → secondary settings → buttons. `set_tab_order()` sets it from a list.
- **First focus** goes to the field the user most likely changes first: the list of columns in
  Calculate, the Find box in Bulk rename, the delimiter in Import.
- **Choice tabs**: Ctrl+Tab and Ctrl+Shift+Tab move to the next and previous tab (`choice_tabs()`
  sets this up). Arrow keys work when the tab bar has focus.
- **Enter** runs the OK button, except in multi-line editors. **Esc** cancels. **Space** toggles the
  focused check box or tree item.
- **Lists in dialogs**: Enter or a double-click runs the list's main action (insert, edit).
- **Every dialog has an action** in `ActionRegistry` with a category, so it appears in the
  menus and the command palette (Ctrl+K). Shortcuts in use:

  | Shortcut | Action |
  | --- | --- |
  | Ctrl+K | Command palette |
  | Ctrl+, | Settings |
  | Ctrl+Shift+I | Import data |
  | Ctrl+Shift+K | Calculate |
  | Ctrl+Shift+E | Edit calculation |
  | — | Bulk rename, Constants (menu and palette only) |

  Reserved for later steps: Ctrl+Shift+A *Analyze*, Ctrl+Shift+F *Format graph*, Ctrl+Shift+X
  *Export graph*. A new shortcut must not clash with these or with platform standard keys.
- **Accessible names**: every control has a buddy label, visible text or `setAccessibleName`.
  The tests check this for every dialog.

## 7. Toolbars

Mockup: `docs/mockups/toolbars.html`.

- **Two toolbars in one row** under the menu bar: the **main toolbar** (New project, Open
  project, Save project, Undo, Redo), then the **table toolbar** (Import data, Add column,
  Group as replicates, Set category, Calculate, Plot, Analyze). The table toolbar shows while a
  table is shown; the main toolbar always shows. They are fixed in place (not movable or
  floating).
- **Every button is a registered action** (`ActionRegistry`): the same `QAction` as the menu
  item and the command palette entry. No action exists only in a toolbar.
- **Labels.** Main toolbar: icons only. Their meaning is universal, and the tooltip gives the
  name and shortcut (“Save project (Ctrl+S)”). Table toolbar: icon with the text beside it,
  because these are Curvis's own tasks and need words. The text is the menu text without the
  ellipsis and mnemonic (“Group as replicates”).
- **Plot** has a menu arrow and opens the chart families (XY, Bar, Survival, Interaction).
  This is SPEC.md's toolbar for picking a chart family. A family the table cannot draw is
  disabled, and its tooltip says why (“Needs a Time + Event pair”).
- **Disabled buttons** use the disabled text colour. Their tooltip says why (“Nothing to
  undo”, “Select columns to group”), like a disabled OK button.
- **Narrow windows.** The toolbars keep one row. Buttons that do not fit move into the overflow
  menu (») at the end, in the same order; text never wraps.
- **Icons**: 20 × 20 line icons on a 20 px grid, round caps and joins, no fills. Files:
  `src/curvis/ui/icons/toolbar/` (its README lists them). Two tones, each mapped to a
  palette role when the theme changes, so one file serves both themes:
  - the **object** (folder, page, table, axes) in navy, 1.5 px → `ButtonText`;
  - the **action** (plus, open lid, arrow, folded corner, data in a chart) in teal, 1.5 px →
    `Link` (#007A7A light, #00A6A6 dark; the icon's own teal is too light on a light window).

  No third tone: thin detail such as table grid lines blurs at 20 px, so it is left out.

  Two tones never meet at a joint. An action part is a closed shape with a gap around it
  (the save label, the bars) or a separate stroke (the import arrow). Where it shares an edge
  with the outline, it draws that edge alone (the import fold's diagonal). A project is a
  folder (New project: folder with a plus; Open project: folder with its lid lifted). A table
  tool's icon matches the mark used in the table where there is one (Calculate uses the ƒ of
  calculated columns). Disabled icons use the disabled text colour for both tones, and an
  icon on a highlighted menu row uses `HighlightedText` for both.
- **States**: hover shows a soft square (`Text` at 14 %), pressed at 25 %, and a menu that is
  open stays pressed, the same as the tab close button. Buttons are 28 px high, with 4 px
  between buttons and a separator between groups (file | undo/redo, and the table toolbar's
  data | columns | results groups).
- **Keyboard.** The toolbars are not in the Tab order, because every button has a shortcut or
  menu entry. The tooltips name the shortcut.
- **Open:** a toolbar for graph formatting is not decided yet. It is not in SPEC.md.

