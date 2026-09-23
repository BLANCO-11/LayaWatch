# LayaWatch design language

Version: 0.1 (2026-09-22)
Status: contract. UI behavior and attributes are defined here, not in tickets.
Baseline: `web/design/mock.html` (approved vision mock).

## 0. Principles

1. **Evidence first.** The screen answers an operational question. Numbers before decoration.
2. **Calm surfaces.** One accent per theme, no gradients, no glows, no decorative art, no emoji.
3. **Depth is affordance.** Raised = interactive, sunken = data well, flat = content. A user reads
   what can be clicked from its elevation, not from its color.
4. **Status is never color alone.** Every state carries a word (`200 OK`, `422 reject`, `500 error`).
5. **Density with hierarchy.** 13 px body, tabular numerals, size and weight contrast instead of
   color contrast, dividers instead of card soup.
6. **Motion is a hint, not a show.** 120 ms color/shadow transitions only. Nothing animates layout.
7. **Self-explanatory defaults.** Every list has a filter, every empty state names the next action,
   every destructive action confirms.

## 1. Foundations

### 1.1 Color roles (semantic, never literal)

Components reference roles only. No component may hardcode a hex value.

| Role | Meaning | Dark (Carbon + Gold) | Light (Ivory + Earth) |
|---|---|---|---|
| `--bg` | page canvas | `#0B0B0A` | `#FBFAF5` |
| `--sidebar` | nav rail surface | `#0E0E0D` | `#F7F5ED` |
| `--surface` | card / panel | `#151514` | `#FFFFFF` |
| `--raised` | raised interactive surface (active nav, popover) | `#1D1D1B` | `#FFFFFF` |
| `--line` | hairline divider, card border | `#2B2B29` | `#EAE5D8` |
| `--line-strong` | emphasized border, inactive track | `#474741` | `#D5CCB7` |
| `--text` | primary text | `#EFEDE6` | `#2A2216` |
| `--text-2` | secondary text, table cells | `#B3B1A8` | `#685D48` |
| `--text-3` | labels, meta, axis text | `#7A7974` | `#978B76` |
| `--accent-bar` | interactive fill: chart primary, rails, active nav icon | `#D4A94E` (gold) | `#B4602F` (terracotta) |
| `--accent-text` | links, accent text | `#E2BB63` | `#9A4C26` |
| `--accent-fill` / `-hover` | primary button fill | `#D4A94E` / `#E3BC63` | `#A9502F` / `#8C4123` |
| `--on-accent` | text on accent fill | `#1A160E` | `#FFFFFF` |
| `--gold` | brand mark only | `#C9A24B` | `#A07B22` |
| `--ok` | success, healthy | `#5FA47E` | `#5C7A3E` |
| `--warn` | degraded, rejected, slow | `#E0952F` | `#A9761E` |
| `--err` | failure | `#DA6E57` | `#B23F2C` |
| `--s2` | chart series 2 (metal brass / olive) | `#C98A4E` | `#6E7444` |
| `--s3` | chart series 3 (steel / clay gray) | `#8E959E` | `#9A9080` |
| `--grid` | chart gridline | `#232321` | `#EFEADD` |
| `--tint-hover` | row and item hover wash | `rgba(233,228,214,.045)` | `rgba(74,58,36,.045)` |

Metal tokens (`--metal-brass`, `--metal-steel`, `--metal-silver`) exist in the dark theme for
non-semantic metallic accents (icon strokes, separators, secondary series). They never carry status.

Rules: the accent role marks interactivity only. Status colors mark status only. A series color never
becomes an interactive color. Contrast minimums: 4.5:1 for body text, 3:1 for large text and UI
boundaries; `--text-3` is only for meta labels of 11 px and above on `--surface`/`--bg`.

### 1.2 Type

| Token | Family | Size / line | Weight | Use |
|---|---|---|---|---|
| `--font-display` | `ui-serif, Georgia, Iowan Old Style, Liberation Serif, serif` | 20 to 24 px | 700 | brand wordmark, page titles |
| `--font-sans` | `system-ui, -apple-system, Segoe UI, Roboto, sans-serif` | 13.5 to 14 px / 1.5 | 400 | body, controls, tables |
| `--font-mono` | `ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace` | 11 to 12.5 px | 400 / 500 | numbers, ids, logs, eyebrows, units |

Scale: `11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 14 / 16 / 20 / 21 / 24`. Numerals are always
`font-variant-numeric: tabular-nums`. Labels are uppercase mono with `letter-spacing: .09em` at
10.5 to 11 px. Never use italic for emphasis; use weight or color role.

### 1.3 Space, radius, borders, elevation

- Space scale: `4 / 8 / 12 / 16 / 24 / 32 / 48` (`--sp1..--sp6`). No arbitrary values.
- Radius lock: `--r-pill 9999px` (badges, pills, switch), `--r-card 6px` (cards, panels),
  `--r-ctl 4px` (buttons, inputs, nav items), `--r-sm 3px` (chips, wells, bars). Never mix
  within one component family.
- Borders: exactly one weight, `1px`. Dashed only for "shown once" secrets and drop targets.
- Elevation tokens:
  - `--shadow-card`: resting card. Dark adds a 1 px top highlight because shadows are invisible on ink.
  - `--shadow-lift`: hover of an interactive raised surface.
  - `--shadow-pop`: popovers, menus, dialogs.
  - `--shadow-inset`: sunken wells (inputs, textareas, log viewer, bar tracks).
- The depth law: **raised** (card, button, active nav, popover) is interactive or a container of
  interactive things; **sunken** (input, well, track, log viewer) is data you supply or read;
  **flat** (KPI strip, table rows, page background) is content you scan.

### 1.4 Motion

- One duration: `120ms`, one easing: `ease`. Allowed properties: `color`, `background`, `border-color`,
  `box-shadow`, `opacity`, `transform` on switch knobs only.
- Never animate layout: no width/height/top/left/margin/position transitions, no page transitions.
- Hover brightens or lifts one shadow step. Active state is a 1 px visual press (no scale).
- Exactly one keyframe in the codebase: skeleton shimmer, disabled under
  `@media (prefers-reduced-motion: reduce)`. All transitions are disabled under that query.

### 1.5 Iconography

- Geometric line icons only, 16 px default (20 px in empty states), stroke 1.5 px, `currentColor`.
- Icons support a text label; an icon-only control requires `aria-label` and a tooltip.
- No illustration, no mascots, no hand-drawn SVG art, no emoji.

### 1.6 Grid and breakpoints

| Breakpoint | Shell | Grid |
|---|---|---|
| >= 1080 px | sidebar 232 px (64 px icon rail at rest, expands on hover or focus), topbar 56 px sticky | KPI 4 columns, charts 2 columns, content max 1440 px |
| 900 to 1080 px | sidebar as above, charts collapse to 1 column | KPI 4 columns |
| 640 to 900 px | sidebar becomes horizontal scrollable nav under the topbar, sticky headers off | KPI 2 columns |
| < 640 px | same as above, content padding 16 px | KPI 1 column, definition lists stack |

## 2. Theming

- Themes are `dark` (Carbon + Gold) and `light` (Ivory + Earth accents).
- `<html data-theme="dark|light">`, resolved in a pre-paint inline script: stored preference, else
  `prefers-color-scheme`, else dark.
- Toggle lives in the topbar (icon button) and mirrors a `system | dark | light` select in Settings.
- Theme changes are instant: token swap only, no transition, no flash.
- Every component must be verified in both themes before a phase gate; a component that reads poorly
  in either theme is incomplete.
- `color-scheme` is set per theme so native controls, scrollbars and form widgets match.

## 3. Layout system

```
+----------------+-------------------------------------------------------+
| sidebar 232    | topbar 56: eyebrow + serif page title | live + theme  |
| brand          +-------------------------------------------------------+
| OBSERVE        | content (max 1440, padding 24)                        |
|   Overview     |   section: title + meta, then a flat or raised block  |
|   Traces       |   section ...                                         |
|   Metrics      |                                                       |
|   Logs         |                                                       |
| OPERATE        |                                                       |
|   Playground   |                                                       |
|   Checkpoints  |                                                       |
| ADMIN          |                                                       |
|   API Keys     |                                                       |
|   Users        |                                                       |
|   Settings     |                                                       |
| footer: poll / |                                                       |
| auth / uptime  |                                                       |
+----------------+-------------------------------------------------------+
```

- Nav groups are fixed: **Observe** (Overview, Traces, Metrics, Logs), **Operate** (Playground,
  Checkpoints), **Admin** (API Keys, Users, Settings).
- Active nav item: raised surface, accent rail on the left, accent icon, weight 600. Exactly one active.
- Every entry carries a 16 px geometric line icon (1.5). At >= 901 px the sidebar rests as a 64 px
  icon rail: entry icons only, brand mark and icons on one centred column, expanding to 232 px on
  pointer hover or keyboard focus. Collapsed state keys off `:focus-visible`, never `:focus-within`,
  so a mouse click on a nav link does not latch the rail open after navigation.
- The sidebar footer is the always-visible health block: poll mode, auth state, uptime, device, ring
  size. It is never hidden except under 900 px.
- Page header pattern: mono eyebrow (group name) plus serif title, single line, no breadcrumbs for
  top-level pages. Detail pages (`/traces/[id]`) add a back link as a ghost button before the eyebrow.
- Section pattern: title left, meta right (`rolling 5-minute windows`), then one block. Sections are
  separated by 32 px, never by cards.

## 4. Component catalog

Each entry defines anatomy, attributes, behavior, accessibility and content rules. Sizes: `sm` 28 px,
`md` 34 px height for controls.

### 4.1 Button

- **Anatomy:** container (radius 4, height 34/28, padding 16/12) plus optional leading icon and
  label.
- **Variants:** `primary` (accent fill, `--on-accent` text, shadow-card, hover shadow-lift),
  `secondary` (raised surface, `--line` border, shadow-card), `ghost` (transparent, `--text-2`, hover
  `--tint-hover`), `danger` (transparent, `--err` text, 40 % `--err` border, hover 10 % `--err` wash).
- **States:** default, hover, active (no transition, 1 px press), focus-visible (2 px accent ring,
  offset 2), disabled (40 % opacity, `cursor: not-allowed`, no hover), loading (label swaps to
  `Working` plus a 3-dot shimmer; width locked to prevent layout shift).
- **Behavior:** one primary button per view. Destructive actions require a confirm dialog. Buttons
  never navigate without an explicit action verb label.
- **A11y:** `<button>` with a real label; icon-only buttons need `aria-label`; `aria-busy` while
  loading; disabled uses the `disabled` attribute, never pointer-events tricks.
- **Content:** sentence case verb plus object (`Create key`, `Revoke`, `Sign out`). No "Submit".

### 4.2 Icon button

Same as Button with a single 16 px icon, 28 or 34 px square, `--r-ctl`, tooltip on hover and focus.

### 4.3 Input, Textarea, Select, Search

- **Anatomy:** mono uppercase label (10.5 px, `--text-3`), control, optional hint or error line.
- **Attributes:** `md` 34 px, `sm` 28 px; `search` adds a leading magnifier icon and a clear button.
- **Style:** sunken: `--bg` fill, `--line` border, `--shadow-inset`. Hover `--line-strong`. Focus:
  accent border plus 3 px 22 % accent ring, `outline: none`.
- **Behavior:** labels are always visible (no placeholder-as-label). Validation runs on blur and on
  submit, error text replaces the hint, never both. Selects are native `<select>` styled to tokens;
  no custom listbox in v0.1. Textareas are mono, 12.5 px, resizable vertically.
- **A11y:** label bound with `for`/`id`; errors referenced with `aria-describedby` and
  `aria-invalid`; search has `role="searchbox"` semantics via `type="search"`.

### 4.4 Switch and Checkbox

- Switch: 36x20 track (`--line-strong` off, `--accent-fill` on), 16 px knob, `transform` transition
  only. Always paired with a text label describing the state that is being enabled.
- Checkbox: 16 px, radius 4, accent fill when checked, same focus ring as inputs.
- Both are `<input>` elements with a styled track; the label is clickable; `:focus-visible` rings.

### 4.5 Segmented control

Inline single-select for 2 to 5 short options (time ranges, view modes). Raised container, active
segment is a raised pill inside it, height 28 px. Never used for navigation.

### 4.6 Badge

- Outline pill, height 22 px, mono 11 px, `color` plus a word.
- Variants: `ok`, `warn`, `err`, `neutral`, `accent`. Border and background are color-mixed from the
  status role at 45 % and 12 %.
- **Content:** `200 OK`, `422 reject`, `500 error`, `active`, `revoked`, `loaded`, `available`. The
  number or state word is mandatory; a bare colored dot is not a badge.

### 4.7 Chip and Tag

Chip: sunken 24 px pill (`--r-sm`) carrying a key/value pair in mono (`model english`). Used in trace
detail headers and filters. Tags (free-form labels on traces) reuse the chip with an `x` affordance
and keyboard removal.

### 4.8 Card and Panel

- **Anatomy:** optional header (title mono uppercase 11 px, meta right, bottom hairline), body
  (padding 16), optional footer.
- **Attributes:** `flat` (no shadow, `--line` border, for KPI strip and grouped content) and `raised`
  (default, `--surface`, shadow-card). `inset` for wells.
- **Behavior:** cards are containers, not buttons. A clickable card must expose a real link or button
  inside; whole-card click is allowed only when the card has exactly one action and shows hover
  `shadow-lift` plus a focus ring.
- **Rules:** never nest raised cards. A raised card inside a raised card is a defect.

### 4.9 Section header

Mono eyebrow (`Observe`, `rolling 5-minute windows`) plus serif title 16 px; meta right-aligned in
mono 11 px `--text-3`. Used once per content block. Not interactive.

### 4.10 KPI cell and KPI strip

- **Anatomy:** strip is a flat bordered container, radius 6, divided by 1 px lines; each cell: mono
  uppercase label, value (mono 24 px, weight 600, tabular), delta line (mono 11 px, `--text-3`, or
  `ok`/`err`/`warn` when the delta has a direction).
- **Attributes:** 4 columns desktop, 2 tablet, 1 mobile. Units are inline in a smaller `--text-3` span.
- **Behavior:** no shadow, no hover, no interaction. Clicking a KPI is out of scope; drill-down is a
  link in the section header.
- **Content:** value plus unit plus window (`412.5 / 863.1 ms`). Delta states its comparison
  (`+0.4 vs prev 5m`), never a bare arrow.

### 4.11 Table

- **Anatomy:** sticky header (mono uppercase 11 px, `--text-3`, bottom hairline, sticks below the
  topbar at 56 px), rows 11 px vertical padding, optional row actions cell.
- **Attributes:** numeric columns right-aligned and tabular; ids in `--accent-text` mono; status
  column uses Badge; the row hover wash is `--tint-hover`.
- **Behavior:** row click opens the detail route when the row represents an entity (trace, key, user);
  the whole row is focusable via the first cell link, not a div click handler. Sorting is available on
  time and numeric columns only, indicated by a caret in the header. Column visibility is fixed in
  v0.1.
- **States:** loading (6 skeleton rows), empty (EmptyState inside the card), error (ErrorState with
  retry), and "filtered to nothing" (empty state with a clear-filters action).
- **A11y:** real `<table>` with `<th scope="col">`, `aria-sort` on sortable headers, `caption` for
  screen readers when the section header is not adjacent.

### 4.12 Pagination and cursor controls

`Load older` secondary button plus a mono range label (`showing 50 of 1,204`). Cursor-based, no page
numbers. Infinite scroll is not used; explicit action keeps the operator oriented.

### 4.13 Filter bar

A row of labelled sunken controls (`route`, `status`, `model`, `request id`, time range) above a
table or chart. Filters are reflected in the URL query string so a view is shareable and reloadable.
`Clear all` ghost button appears only when at least one filter is set. Debounce text filters by 250 ms.

### 4.14 Chart (area, line, stacked mix)

- **Anatomy:** raised card, mono title, current value right, plot, legend chips, footer with window
  and unit.
- **Rules:** gridlines from `--grid` at 25/50/75 %; no vertical gridlines; axes labels mono 11 px
  `--text-3`; series colors from `--accent-bar`, `--s2`, `--s3`; a series that represents health
  (errors, queue) uses `--err` / `--warn`; area fill is the series color at 14 % opacity, never a
  gradient; no 3D, no drop shadows, no smoothing tricks that hide spikes.
- **Behavior:** hover shows a crosshair with a mono tooltip (time, series values); tooltips are the
  only floating element allowed inside a chart. Legend chips toggle series visibility; toggling is
  instant and does not reorder the chart.
- **States:** empty (`No data in this window`), single-point, and gap handling (gaps render as breaks,
  not interpolation).
- **A11y:** each chart exposes a text summary (latest, min, max, window) in `aria-label` and a
  visually hidden `<table>` alternative for the current window.

### 4.15 Waterfall

- **Anatomy:** one row per span: mono name (110 px), start offset (64 px, right), track (1fr), duration
  (64 px, right). Track is a sunken well, bars are 14 px tall with 3 px radius.
- **Colors:** standard spans `--accent-bar`; queue wait `--warn`; forward pass `--s2`; error span
  `--err`. Bar width is proportional to duration against the trace total; a minimum 2 px width keeps
  sub-millisecond spans visible.
- **Behavior:** hovering a row highlights the bar and shows a tooltip with absolute start and duration;
  clicking a span expands its metadata (attributes JSON, truncated) inline, one at a time.
- **Rules:** the trace summary line under the waterfall states total, forward share and queue share,
  plus status and absolute start time. Never rely on bar length alone: every row prints its duration.
- **A11y:** `<ol>` semantics with each row an `<li>` carrying `aria-label` of name, start and duration.

### 4.16 Log viewer

- Sunken well (`--shadow-inset`), mono 12 px, line height 1.9, horizontal scroll, `white-space: pre`.
- Severity tokens: timestamp `--text-3`, request id `--accent-text`, ok `--ok`, warn `--warn`,
  error `--err`, numbers `--text`.
- Behavior: `Pause` freezes the tail without disconnecting (buffered count is shown), `Clear` empties
  the local view only, `Jump to latest` returns to follow mode. Filtering is server-side (level,
  substring, request id) with the same debounce as other filters.
- Never color a whole line; color only the severity token and id.

### 4.17 Timeline (trace detail side rail)

Vertical list of events and scores for one trace: icon column (16 px), mono timestamp, text. Used for
score submissions and lifecycle events (model loaded, retention applied).

### 4.18 Dropdown menu

Raised popover (`--shadow-pop`, radius 6, 4 px inner padding), items 30 px, mono or sans 13 px,
destructive items in `--err`. Opens on click, closes on select, `Escape`, or outside click. Keyboard:
arrows move, `Enter` selects, `Home`/`End` jump. Anchored to the trigger, never centered.

### 4.19 Dialog (modal)

Centered, max width 480 px (confirm) or 640 px (form), `--surface`, radius 6, shadow-pop, scrim
`rgba(0,0,0,.5)` dark / `rgba(40,32,20,.35)` light. Title serif 16 px, body 13.5 px, footer right
aligned with one primary action. Focus is trapped, `Escape` cancels, initial focus lands on the least
destructive control. Destructive dialogs name the object (`Revoke lay_3f8a...`) and require an explicit
verb button.

### 4.20 Drawer

Right-side panel, width 420 px (100 % under 640 px), used for create/edit forms that need more room
than a dialog. Same focus and escape rules as Dialog.

### 4.21 Toast

Bottom right stack, max 3 visible, raised surface, left accent bar by severity, mono title plus one
line of body, auto-dismiss 6 s (errors persist until dismissed). Toasts never carry the only copy of
an important value; secrets are shown in an inline `SecretReveal` instead.

### 4.22 Banner and Alert

Full-width flat block with a 3 px left border in the severity color: `info`, `warn`, `err`. Used for
degraded states (`Multilingual cannot be loaded while LAYA_ENGLISH_ONLY=1`) and for onboarding hints.
Banners are dismissible per session; never stack more than one.

### 4.23 Tooltip

Dark-on-light inverted raised bubble, mono 11.5 px, 6 px radius, max 240 px, opens after 150 ms on
hover or immediately on focus. Never contains interactive content or the only copy of a value.

### 4.24 Empty state

Centered, 20 px icon in `--text-3`, serif 16 px title, 13.5 px `--text-3` body naming the next action,
one secondary button. Used for every list, chart and log view. Copy example: `No traces yet. Send a
request to /predict to see one.`

### 4.25 Loading and skeleton

Skeleton blocks use the single shimmer keyframe, `--line` base, radius matching the real element, and
mirror the final layout. Skeletons are used for content taking longer than 300 ms; shorter waits show
nothing. Charts and tables always use skeletons, never spinners. A global route change shows a 2 px
accent progress bar under the topbar.

### 4.26 Error state

Inline block replacing the failed region: `--err` left border, mono title (`Failed to load traces`),
one line with the HTTP status and code, `Retry` secondary button. Server errors never surface stack
traces in the UI; the request id is shown and copyable for log correlation.

### 4.27 Key-value list (definition list)

Two columns: mono uppercase key 160 px (`--text-3`), mono value. Hairline between rows, last row
borderless. Used for server facts, settings summaries, trace metadata. Values that are secrets use
SecretReveal; values that are ids use CopyField.

### 4.28 Secret reveal

Dashed accent border, accent-tinted sunken background, mono secret, `Copy` secondary button, and a
mandatory note `shown once, store it now`. Never auto-dismisses, never leaves the page without an
explicit action.

### 4.29 Copy field

Mono value plus a ghost `Copy` button that swaps to `Copied` for 1.5 s. Copy failures surface a toast.

### 4.30 Avatar and user menu

Avatar: 28 px circle, initials in mono on `--raised`, accent ring when the user menu is open. User
menu is a DropdownMenu with `Account`, `Theme`, `Sign out`. Users list shows avatar plus name plus
role badge.

### 4.31 Audit row

Table row variant: mono timestamp, actor (avatar plus name), action verb (`key.created`,
`model.load`, `user.role_changed`), target mono id, result badge. Read-only, filterable by actor and
action, never editable.

### 4.32 Status dot

6 px dot, `--ok` / `--warn` / `--err`, always adjacent to a word (`polling 3s`, `auth ON`). A dot
alone is only allowed inside a live pill that carries text.

## 5. Data visualization rules

1. Time series use the step from the API; never re-bucket on the client.
2. Y axes start at zero for counts and rates; latency charts may start at zero and must state the max.
3. Percentiles are lines with the legend naming each series (`p50`, `p90`, `p95`); never stack them.
4. The error chart uses `--err`; the queue chart uses `--warn`; both are health signals, not series
   colors, and are exempt from the accent-only-interactive rule.
5. Model mix is a stacked bar per bucket with a legend naming each model and a total label on hover.
6. Every chart states its window and step in the footer (`step 10s · window 15m`).
7. Missing data renders as a gap plus a footer note; never interpolate across a gap silently.
8. Thresholds (p95 > 1 s, error rate > 2 %) are stated in the section meta, not drawn as colored
   zones.

## 6. Content and microcopy

- Sentence case everywhere except mono labels and badges, which are uppercase or exact values.
- No em-dashes, no emoji, no exclamation marks, no marketing adjectives.
- Numbers: thousands separators (`48,213`), one decimal for ms and MB (`412.5 ms`, `2847.3 MB`),
  durations as `14h22m`, timestamps ISO 8601 with seconds plus timezone in tooltips.
- Errors: state what failed, why, and what to do (`Auth is required. Add X-API-Key to the request.`).
- Empty states name the next action; destructive confirmations name the object and the consequence.
- Every value that a user may need to paste is copyable, and secrets are shown exactly once.

## 7. Accessibility

- Contrast: body text >= 4.5:1, large text and borders >= 3:1, focus ring >= 3:1 against both the
  control and the surrounding surface, in both themes.
- Focus: visible 2 px accent ring with 2 px offset on every interactive element; focus order follows
  reading order; the skip link (`Skip to content`) is the first focusable element.
- Keyboard: every action reachable without a pointer; tables navigable by row links; dialogs trap
  focus and restore it on close; menus support arrows, `Enter`, `Escape`.
- Streaming: the live pill is `aria-live="polite"`; new rows are announced as a count
  (`12 new traces`), never row by row.
- Charts and waterfalls expose a text summary; the waterfall prints every duration as text.
- Reduced motion: shimmer, knob transitions and hover lifts are disabled.
- Forms: errors are announced, never color-only; required fields are marked in the label text.

## 8. Implementation contract

- Tokens live in `web/src/styles/tokens.css` and are the only place hex values exist.
- Components live in `web/src/components/ui/` as one file per component exporting a typed props
  interface; no CSS-in-JS runtime, no utility framework, plain CSS with token variables.
- Component props follow the attributes named here (`variant`, `size`, `state`); a prop that is not in
  this document must be added here first.
- Charts are hand-built SVG components (`web/src/components/charts/`); no chart library.
- Every component ships with both themes verified and a keyboard pass; the phase gate checks the
  catalog list in this document against implemented files.
- The design language version bumps when a component's attributes or behavior change; the change is
  recorded in the active phase's `decisions.md`.
