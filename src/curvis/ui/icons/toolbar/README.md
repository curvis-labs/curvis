# Toolbar icons

20 × 20 line icons for the main and table toolbars (docs/DESIGN.md, section 7).
Each file uses three colours, which the app replaces with palette colours for the theme:

| Colour in the file | Part | Palette role |
| --- | --- | --- |
| `#1d3557` navy, 1.5 px | the object's outline | `ButtonText` |
| `#00a6a6` teal, 1.5 px | the action (plus, lid, arrow, data) | `Link` |
| `#667085` slate, 1 px | detail (table grid lines) | `PlaceholderText` |

Disabled icons use the disabled text colour for all three; on a highlighted menu row all
three use `HighlightedText`. Two colours never meet at a
joint: an action part is either a closed shape with a gap around it or crosses cleanly.
