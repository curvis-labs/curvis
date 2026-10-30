# Toolbar icons

20 × 20 line icons for the main and table toolbars (docs/DESIGN.md, section 7).
Each file uses two colours, which the app replaces with palette colours for the theme:

| Colour in the file | Part | Palette role |
| --- | --- | --- |
| `#1d3557` navy, 1.5 px | the object's outline | `ButtonText` |
| `#00a6a6` teal, 1.5 px | the action (plus, lid, arrow, data) | `Link` |

Disabled icons use the disabled text colour for both; on a highlighted menu row both use
`HighlightedText`. Thin detail such as grid lines is left out: it blurs at 20 px. The two
colours never meet at a joint: an action part is either a closed shape with a gap around
it or a separate stroke.
