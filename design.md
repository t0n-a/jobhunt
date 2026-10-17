# jobhunt design system

Locked from demo F (`design-demos/f-blend.html`). Every page follows this; a page that needs
something new adds it here first.

## Feel
A night-sky working tool: calm panels on a deep blue-violet ground, one soft glow at the top,
colour reserved for status. Dense but readable. Not a terminal, not a marketing site.

## Type
- One family: **Bricolage Grotesque** (Google Fonts), fallback `system-ui`. Weights 400 / 500 / 700 / 800.
- Numbers are `tabular-nums` everywhere (fit scores, counts).
- Page titles 28–34px / 800 / -0.02em. Panel titles 17px / 700. Body 15px / 1.5. Meta 13px, `--ink-soft`.
- Headings are never italic. Status labels are lowercase.

## Colour (tokens in `app.css` `:root`)
Light and dark both follow `prefers-color-scheme` (light is the base, dark overrides). The old names (`--bg`, `--surface`, `--surface-2`, `--accent`, `--shadow`) are aliases kept for compatibility.

| Token | Use |
|---|---|
| `--paper`, `--panel`, `--panel-2`, `--line` | page, panels, raised/hover, hairlines |
| `--ink`, `--ink-soft` | text, secondary text |
| `--focus` | focus ring, selected row edge (amber in dark, indigo in light) |
| `--bar`, `--bar-bg` | fit bar and fit ring |
| `--st-unscored` `--st-ranked` `--st-drafted` `--st-waiting` `--st-sent` `--st-blocked` `--st-closed` | one colour per status |
| `--waiting-bg`, `--blocked-bg` | tinted rows and cards for the two states that need the user |
| `--flag`, `--flag-bg` | unverified / flagged text |
| `--ok`, `--ok-bg` | success flashes, verified marks |

## Status system
Every job shows exactly one status and one next action (`jobhunt.web.inbox.job_state`).

| key | label | means | next action |
|---|---|---|---|
| unscored | unscored | found, not scored | open it |
| ranked | ranked | scored, no application | tailor a resume |
| drafted | drafted | the user tailored it and has not sent it | finish and apply |
| blocked | blocked | drafted or queued, but the form has required questions with no answer | answer them |
| waiting | needs your yes | autopilot tailored it (status `queued`) and waits for approve / skip | approve or skip |
| approved | approved | approved, waiting to send | none |
| sent | sent / replied / interview / offer | out the door | view it |
| closed | rejected / ghosted / skipped | finished | none |

- **Needs-you states** (`blocked`, `waiting`) are loud: a "Needs you" strip of tinted cards at the top of the
  page, tinted rows with a 4px coloured left edge, and a filled pill. Blocked sorts before waiting.
- Every other state is quiet: a glowing 8px dot plus lowercase label.

## Components
- **Panel**: `--panel`, 1px `--line`, radius 16px. The job card is radius 24px.
- **Fit bar**: 8px rounded track (`--bar-bg`) with a `--bar` fill, number to its right.
- **Fit ring**: 84px conic ring, number centred.
- **Status pill** `.st` (dot + label); `.st.hot` is the filled variant for needs-you states.
- **Needs-you card** `.need`: tinted background, 6px left edge in the status colour, uppercase micro-label,
  title, company · fit, the problem, and one filled button for the next action.
- **Flag box** `.flag`: `--flag-bg`, 1.5px `--flag` border, uppercase "Check this before it goes out",
  the flagged text with a wavy `--flag` underline.
- **Buttons**: radius 12–14px. `.primary` is the solid `--go` button, one per view. Others are `--panel-2`.
- **Fit map**: secondary, never the centre of a page. SVG, y = fit 40–100, x = age (older left), a soft band
  over fit 80–100, dots coloured by status, dashed `--flag` ring when the job has flagged text.

## Layout
- Header: wordmark, tab nav (current tab on `--panel-2`), user name right.
- Wide pages (Jobs, Applications, Counselor) use the two-column workbench: list left (1.35fr), sticky card right (1fr).
  Text pages stay at a 44rem measure.
- Below 900px everything stacks to one column with 16px gutters; no horizontal page scroll.

## Motion
Only `transform` / `opacity`, 120–150ms, `cubic-bezier(.2,.8,.2,1)`. Nothing moves under
`prefers-reduced-motion`. Focus rings appear instantly.

## Accessibility
Body text ≥ 4.5:1 on its background in both modes. `:focus-visible` ring 2px `--focus`. Status is never
colour alone: every dot has a text label.
