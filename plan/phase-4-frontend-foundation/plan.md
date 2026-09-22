# Phase 4 - Frontend foundation

Status: planned
Depends on: phase 3
Estimated effort: 6 to 8 days
Deliverable: `web/` builds a static-exported Next.js console whose shell, full component catalog,
both themes, auth screens and quality checks are live and served by the Python process from `web/out`.

## Objective

Build the frontend foundation the console views stand on: the Next.js App Router project with
`output: "export"`, the token stylesheet ported from the approved mock, the component catalog from
`docs/design-language.md` section 4, the app shell from section 3, the auth UI over the Phase 3
endpoints, and the a11y, bundle and Playwright quality bars that Phases 5 and 6 inherit.

## Scope

In: `web/` scaffold (Next.js App Router, TypeScript, plain CSS with token variables, no CSS
framework, static export to `web/out` served by `layawatch/http/static.py`); `web/src/styles/tokens.css`
with every role of `docs/design-language.md` section 1, both themes, pre-paint `data-theme`
resolution and `prefers-reduced-motion` handling; one component per family in
`web/src/components/ui/` plus hand-built SVG chart primitives in `web/src/components/charts/`;
app shell with the fixed nav groups, topbar, health footer, responsive matrix and skip link; login,
first-run setup wizard, session guard with CSRF-aware fetch client, user menu sign out;
empty/loading/error state patterns; component catalog route; bundle, a11y and Lighthouse (or
equivalent) checks; Playwright harness.

Out: view contents and data wiring for Overview, Traces, Metrics, Logs (Phase 5); Playground,
Checkpoints, API Keys, Users, Audit, Settings contents and the Settings `system | dark | light`
select (Phase 6); SSE data rendering beyond the shell live pill connection state (Phase 5);
performance optimization beyond the initial bundle ceiling (Phase 7); any Python runtime change or
new runtime dependency (Node stays build-time only, per decisions D-001 and D-002).

## Deliverables

```
web/package.json              next/react/react-dom as pinned devDependencies, package-lock.json committed
web/tsconfig.json             strict TypeScript, no emit
web/next.config.mjs           output: "export", images unoptimized, build writes web/out
web/public/theme-init.js      blocking pre-paint data-theme resolver (D-012)
web/src/app/layout.tsx        root layout: theme script, skip link, shell mount
web/src/app/page.tsx          Overview route shell (content lands in Phase 5)
web/src/app/traces/           list shell plus [id]/ detail shell
web/src/app/{metrics,logs,playground,checkpoints,keys,users,settings}/  route shells
web/src/app/login/page.tsx    login screen
web/src/app/setup/page.tsx    first-run owner wizard
web/src/app/dev/catalog/      component catalog route used by the gate
web/src/styles/tokens.css     every role from design-language section 1, both themes, only hex source
web/src/styles/base.css       type scale, focus ring, reduced-motion, tabular numerals
web/src/components/shell/     Sidebar, Topbar, HealthFooter, LivePill, SkipLink, nav.ts, shell.css
web/src/components/ui/        one .tsx plus co-located .css per catalog family (section 4)
web/src/components/charts/    AreaChart, LineChart, StackedBar, Axis, Legend, Crosshair (section 8)
web/src/lib/api.ts            CSRF-aware fetch client, error envelope parsing, 401 signal
web/src/lib/filters.ts        URL-synced filter state with 250 ms debounce
tests/ui/                     Playwright harness: auth, navigation, theme, keyboard matrix, states
scripts/web_check.py          catalog, bundle, a11y subcommands
Makefile                      test-ui and web-check targets
```

## Tasks

1. Scaffold `web/`: `web/package.json` with `next`, `react`, `react-dom` pinned in devDependencies
   only, `package-lock.json` committed; `web/tsconfig.json` with strict mode; `web/next.config.mjs`
   with `output: "export"` and `images.unoptimized: true` so `npm run build` writes `web/out/`;
   `.gitignore` entries for `web/node_modules` and `web/out`. No CSS framework, no chart library, no
   UI kit anywhere in dependencies. Confirm `layawatch/http/static.py` serves `web/out` unchanged
   (Phase 0 task 7), so this phase touches no Python file.
2. Port `web/src/styles/tokens.css` from `web/design/mock.html`: every color role of
   `docs/design-language.md` section 1.1 under both `[data-theme="dark"]` and
   `[data-theme="light"]`, metal tokens, type tokens (1.2), space and radius and elevation tokens
   (1.3), motion duration and easing (1.4), `color-scheme` per theme. Hex values exist nowhere else.
3. Write `web/src/styles/base.css` and `web/public/theme-init.js`: base font stacks, the 11 to 24 px
   scale, `font-variant-numeric: tabular-nums`, global `:focus-visible` 2 px accent ring with 2 px
   offset, and a `prefers-reduced-motion: reduce` block disabling every transition plus the single
   shimmer keyframe. `theme-init.js` is referenced as a synchronous blocking script in
   `web/src/app/layout.tsx` head and resolves the `laya-theme` localStorage key, else
   `prefers-color-scheme`, else dark onto `<html data-theme>` before first paint.
4. Build the app shell in `web/src/components/shell/`: `nav.ts` holds the fixed groups from
   `docs/design-language.md` section 3 (Observe: Overview, Traces, Metrics, Logs; Operate:
   Playground, Checkpoints; Admin: API Keys, Users, Settings); `Sidebar.tsx` renders raised active
   item with accent rail, accent dot and weight 600, exactly one active; `Topbar.tsx` renders mono
   eyebrow, serif title, `LivePill`, theme toggle icon button; `HealthFooter.tsx` reads
   `GET /api/v1/meta` for poll mode, auth state, uptime, device, ring size and stays visible except
   under 900 px; `SkipLink.tsx` is the first focusable element; `shell.css` implements the
   1080/900/640 responsive matrix of section 1.6.
5. Create the route shells: `web/src/app/page.tsx`, `traces/page.tsx`, `traces/[id]/page.tsx`,
   `metrics/page.tsx`, `logs/page.tsx`, `playground/page.tsx`, `checkpoints/page.tsx`,
   `keys/page.tsx`, `users/page.tsx`, `settings/page.tsx`, each rendering the group eyebrow plus
   serif title via `SectionHeader` and a placeholder empty state; real content and data loading are
   Phase 5 and Phase 6 scope.
6. Auth UI: `web/src/lib/api.ts` typed fetch client that attaches `X-CSRF-Token` read from the
   `lw_csrf` cookie on every mutating request, uses `credentials: "same-origin"`, parses the
   `docs/api-reference.md` section 2 error envelope, and signals session expiry on 401;
   `web/src/components/AuthProvider.tsx` guard that redirects to `/login` on 401 and returns to the
   original path after sign-in; `web/src/app/login/page.tsx` posts
   `POST /api/v1/auth/login` (api-reference section 10) and renders the 429 `Retry-After` message;
   `web/src/app/setup/page.tsx` first-run wizard posts `POST /api/v1/auth/setup`, then routes to
   `/login`, and renders the closed-setup error state with a link to `/login` when the endpoint
   refuses; `UserMenu` sign out posts `POST /api/v1/auth/logout`.
7. Button and icon button: `web/src/components/ui/Button.tsx`, `IconButton.tsx`, `button.css`.
   Variants primary/secondary/ghost/danger, sizes sm 28 and md 34, loading state with locked width,
   `aria-busy`, disabled via the `disabled` attribute, per section 4.1 and 4.2; icon-only controls
   carry `aria-label` plus tooltip.
8. Input, textarea, select, search: `ui/Input.tsx`, `ui/Textarea.tsx`, `ui/Select.tsx`,
   `ui/Search.tsx`, `form.css`. Always-visible mono labels, hint XOR error with `aria-describedby`
   and `aria-invalid`, native `<select>` styled to tokens, `type="search"` with clear button,
   validation on blur and submit, per section 4.3.
9. Switch and checkbox: `ui/Switch.tsx`, `ui/Checkbox.tsx`. Styled `<input>` elements, clickable
   label, `:focus-visible` ring, switch knob uses `transform` transition only, per section 4.4.
10. Segmented control: `ui/Segmented.tsx`. Raised container, raised active pill, 2 to 5 options,
    roving tabindex with arrow keys, never used for navigation, per section 4.5.
11. Badge: `ui/Badge.tsx`. Outline pill 22 px, mono 11 px, variants ok/warn/err/neutral/accent
    color-mixed at 45 percent and 12 percent, mandatory word content, per section 4.6.
12. Chip and tag: `ui/Chip.tsx`, `ui/Tag.tsx`. Sunken 24 px key/value chip; tag reuses it with an
    `x` affordance removable by keyboard, per section 4.7.
13. Card and panel: `ui/Card.tsx`, `card.css`. Flat/raised/inset variants, header plus body plus
    optional footer, single-action card shows hover lift plus focus ring, never a raised card inside
    a raised card, per section 4.8.
14. Section header: `ui/SectionHeader.tsx`. Mono eyebrow plus serif title left, mono meta right,
    not interactive, per section 4.9.
15. KPI cell and strip: `ui/KpiStrip.tsx`, `ui/KpiCell.tsx`. Flat divided strip, mono label,
    tabular value with inline unit span, delta line naming the comparison, 4/2/1 columns at the
    breakpoints, no hover, per section 4.10.
16. Table: `ui/Table.tsx`, `table.css`. Real `<table>` with `<th scope="col">`, sticky header at
    56 px below the topbar, `aria-sort` on sortable time and numeric columns, first-cell link as
    the focusable row entry, `--tint-hover` wash, right-aligned tabular numerals, and the four
    states: 6-row skeleton, empty (EmptyState inside the card), error (ErrorState with retry),
    filtered-to-nothing (empty state with clear-filters), per section 4.11.
17. Pagination: `ui/Pagination.tsx`. `Load older` secondary button plus mono range label, cursor
    semantics in props, no page numbers, no infinite scroll, per section 4.12.
18. Filter bar: `ui/FilterBar.tsx` plus `web/src/lib/filters.ts`. Labelled sunken controls
    (route, status, model, request id, time range), filters mirrored into the URL query string,
    `Clear all` ghost only when a filter is set, 250 ms debounce on text inputs, per section 4.13.
19. Chart primitives: `ui/Chart.tsx` for card chrome (title, current value, legend, footer with
    window and unit) plus `charts/AreaChart.tsx`, `charts/LineChart.tsx`, `charts/StackedBar.tsx`,
    `charts/Axis.tsx`, `charts/Legend.tsx`, `charts/Crosshair.tsx` as hand-built SVG with no chart
    library, per sections 4.14, 5 and 8: gridlines at 25/50/75 percent only, series from
    `--accent-bar`/`--s2`/`--s3`, health series from `--err`/`--warn`, 14 percent flat area fill,
    gaps render as breaks, legend chips toggle series instantly, crosshair mono tooltip, `aria-label`
    summary plus a visually hidden data table.
20. Waterfall: `ui/Waterfall.tsx`. Row anatomy (name 110 px, start 64 px, track 1fr, duration
    64 px), bar colors standard `--accent-bar`, queue `--warn`, forward `--s2`, error `--err`,
    2 px minimum bar width, hover highlight with tooltip, one expanded span at a time, `<ol>` with
    per-row `aria-label`, every duration printed as text plus the summary line, per section 4.15.
21. Log viewer: `ui/LogViewer.tsx`. Sunken well, mono 12 px, line height 1.9, horizontal scroll,
    severity token and id coloring only (never a whole line), `Pause` with buffered count, `Clear`
    local only, `Jump to latest`, per section 4.16.
22. Timeline: `ui/Timeline.tsx`. Icon column 16 px, mono timestamp, text row, used for scores and
    lifecycle events, per section 4.17.
23. Dropdown menu: `ui/Dropdown.tsx`. Anchored raised popover, 30 px items, destructive items in
    `--err`, closes on select, `Escape` or outside click, arrows plus `Home`/`End` navigation, focus
    restore to the trigger, per section 4.18.
24. Dialog: `ui/Dialog.tsx`. Widths 480/640, theme-correct scrim, serif title, focus trapped,
    `Escape` cancels, initial focus on the least destructive control, focus restored on close,
    destructive dialogs name the object and use an explicit verb button, per section 4.19.
25. Drawer: `ui/Drawer.tsx`. Right panel 420 px, full width under 640 px, same focus and escape
    rules as Dialog, per section 4.20.
26. Toast: `ui/ToastProvider.tsx`, `ui/Toast.tsx`. Bottom-right stack capped at 3, severity left
    accent bar, mono title plus one body line, 6 s auto-dismiss with errors persisting, announced
    through an `aria-live="polite"` region, never carries secret values, per section 4.21.
27. Banner: `ui/Banner.tsx`. Flat full-width block with 3 px severity border, info/warn/err,
    one banner at a time, per-session dismissal, per section 4.22.
28. Tooltip: `ui/Tooltip.tsx`. Mono 11.5 px bubble, max 240 px, 150 ms hover delay, immediate on
    focus, no interactive content inside, per section 4.23.
29. Empty state: `ui/EmptyState.tsx`. 20 px icon in `--text-3`, serif 16 px title, body naming the
    next action, one secondary button, per section 4.24.
30. Loading and skeleton: `ui/Skeleton.tsx`, `ui/RouteProgress.tsx`. Single shimmer keyframe on
    `--line` base disabled under reduced motion, skeleton blocks mirror the final layout, content
    under 300 ms shows nothing, 2 px accent route progress bar under the topbar, per section 4.25.
31. Error state: `ui/ErrorState.tsx`. `--err` left border, mono title, status plus code line,
    copyable request id, `Retry` button, never a stack trace, per section 4.26.
32. Key-value list: `ui/KeyValueList.tsx`. Definition list, mono uppercase key column 160 px,
    hairline between rows with the last row borderless, stacks under 640 px, secret values route to
    SecretReveal and ids to CopyField, per section 4.27.
33. Secret reveal: `ui/SecretReveal.tsx`. Dashed accent border, accent-tinted sunken background,
    mono secret, `Copy` button, mandatory `shown once, store it now` note, never auto-dismisses,
    per section 4.28.
34. Copy field: `ui/CopyField.tsx`. Mono value plus ghost `Copy` swapping to `Copied` for 1.5 s,
    copy failure surfaces a toast, per section 4.29.
35. Avatar and user menu: `ui/Avatar.tsx`, `ui/UserMenu.tsx`. 28 px initials circle on `--raised`
    with accent ring while open; menu items `Account`, `Theme`, `Sign out` built on Dropdown, per
    section 4.30.
36. Audit row: `ui/AuditRow.tsx`. Mono timestamp, avatar plus actor, action verb, mono target id,
    result badge, read-only, per section 4.31.
37. Status dot and live pill: `ui/StatusDot.tsx`, `ui/LivePill.tsx`. 6 px dot always adjacent to a
    word; the topbar live pill carries text, uses `aria-live="polite"`, and reflects SSE
    connection state (connected, reconnecting, offline) with reconnect backoff, per sections 4.32
    and 7.
38. Component catalog route: `web/src/app/dev/catalog/page.tsx` renders every family from section 4
    in all variants and states with an in-page theme toggle, so the gate can review the catalog
    without live data.
39. `scripts/web_check.py catalog`: assert that every section 4 family has its
    `web/src/components/ui/<file>.tsx` (chart families under `web/src/components/charts/`) and an
    entry on `/dev/catalog`; the family manifest inside the script mirrors the doc list, so a
    missing component fails the check rather than the gate review.
40. `scripts/web_check.py bundle`: enforce decision D-011 (see Risks) over `web/out`: first-load JS
    per route and total export size, reading `.next` bundle analyzer output or the build manifest,
    exiting non-zero on breach.
41. `scripts/web_check.py a11y`: run axe-core through Playwright against `/login`, a shell route
    and `/dev/catalog` in both themes, and run Lighthouse (`npx lighthouse`, build-time dev tool)
    against the local server; when Lighthouse is unavailable, record the equivalent scripted check
    (axe results plus First Contentful Paint and Total Blocking Time from Playwright) as the same
    JSON artifact under `plan/phase-4-frontend-foundation/evidence/`.
42. Playwright harness: `tests/ui/conftest.py` boots `python -m layawatch` on a temp state dir and
    port and waits for `/healthz`; `tests/ui/test_auth.py` covers redirect to `/login`, login,
    setup wizard, 401 mid-session redirect, the `X-CSRF-Token` header on mutating requests, and
    sign out; `tests/ui/test_navigation.py` covers every nav route, exactly one active item, and
    the skip link; `tests/ui/test_theme.py` covers toggle, persistence across reload and no flash;
    `tests/ui/test_keyboard_matrix.py` walks every catalog component by keyboard in dark and light;
    `tests/ui/test_states.py` asserts one loading, one empty and one error state per view. Wire a
    `make test-ui` target behind a pytest marker so `make test` stays browser-free.
43. Enable the web CI job stubbed in Phase 0 (`.github/workflows/ci.yml`): `npm ci`,
    `npm run build`, then `scripts/web_check.py catalog bundle a11y` on the built export, alongside
    the existing lint and test rows.

## Acceptance criteria

1. `cd web && npm ci && npm run build` exits 0 and writes `web/out/`; with `make dev`,
   `curl -sI http://127.0.0.1:8050/` returns 200 HTML with `no-cache` and a `/_next/static/*`
   asset returns 200 with an immutable cache header.
2. `scripts/web_check.py catalog` lists every family of `docs/design-language.md` section 4 (31
   entries) as present and rendered on `/dev/catalog`; zero missing.
3. Token completeness: every role row of section 1.1 is defined under both `[data-theme="dark"]`
   and `[data-theme="light"]` in `web/src/styles/tokens.css`, and Playwright observes
   `html[data-theme]` already set before first paint with no theme flash on reload under emulated
   `system dark` and `system light`.
4. Both-themes keyboard pass: `tests/ui/test_keyboard_matrix.py` passes in dark and light: every
   interactive catalog component is reachable by `Tab`, operable by its documented keys (`Enter`,
   `Escape`, arrows, `Home`/`End`), and shows the 2 px accent focus ring; dialogs and drawers trap
   focus and restore it to the trigger on close.
5. Shell responsive matrix: Playwright at 1080, 900 and 640 px shows sidebar 232 px fixed at
   >= 900 px, horizontal scrollable nav below 900 px, KPI strip 4/2/1 columns, health footer visible
   at >= 900 px, exactly one active nav item per route, and the skip link as the first focusable
   element.
6. Auth flow: unauthenticated navigation lands on `/login`; a valid login reaches the shell; a 401
   mid-session redirects to `/login` preserving the intended path; network capture shows
   `X-CSRF-Token` on every mutating fetch; `/setup` creates the first owner exactly once and then
   renders the closed error state linking to `/login`; sign out ends at `/login` and the old cookie
   gets 401 from `curl`.
7. Bundle ceiling (D-011): `scripts/web_check.py bundle` exits 0 with first-load JS <= 150 KB gzip
   for every route and total `web/out` <= 3 MB, and exits non-zero when either ceiling is exceeded.
8. Lighthouse or equivalent: on `/login` and a shell route against the local server, Lighthouse
   scores >= 90 for accessibility, best-practices and performance, or the equivalent scripted check
   records axe with zero serious/critical violations plus First Contentful Paint <= 1.5 s and Total
   Blocking Time <= 200 ms, written as JSON to `plan/phase-4-frontend-foundation/evidence/`.
9. State coverage: for each of the eight nav routes, Playwright observes one loading skeleton, one
   empty state naming the next action, and one error state with a working `Retry`; on `/dev/catalog`
   the table, chart and log families each demonstrate all three states.
10. A11y baseline: axe on `/login`, a shell route and `/dev/catalog` in both themes reports zero
    serious or critical violations; the live pill carries `aria-live="polite"`; every chart exposes
    a text summary and a visually hidden data table.

## Evidence required

- `cd web && npm ci && npm run build` output with per-route bundle sizes, plus `du -sb web/out`.
- `python scripts/web_check.py catalog bundle a11y` transcript with the observed numbers.
- Playwright run log for `tests/ui/` (auth, navigation, theme, keyboard matrix, states) with pass
  counts, plus the same run output showing the dark and light passes separately.
- Lighthouse JSON (or the equivalent scripted check JSON) under
  `plan/phase-4-frontend-foundation/evidence/`.
- `curl -sI` header dump for `/` and one `/_next/static/*` asset.
- Screenshots of `/dev/catalog` and the shell at 1080, 900 and 640 px in both themes.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Static export cannot redirect server-side (R-05) | client `AuthProvider` guard plus the server-side 401 on every API call; Playwright covers the 401-mid-session path end to end |
| Bundle grows past any budget as 31 families land | D-011 ceiling asserted by `scripts/web_check.py` in CI from the first commit; no chart or UI libraries allowed (design-language section 8, performance.md section 4.6) |
| Inline theme script would break the CSP in `docs/security.md` section 8.12 (no inline scripts) | D-012: blocking external `web/public/theme-init.js`; the no-flash Playwright check proves it still resolves before paint |
| Token port drifts from `web/design/mock.html` | `tokens.css` is the only file with hex values; rendered roles compared against the Phase 0 tokens-preview page; screenshots recorded as evidence |
| Keyboard traps or focus loss in dialog, drawer and dropdown | focus trap plus focus restore are part of the keyboard matrix test, run in both themes |
| Phase 3 auth endpoint details still moving | contract pinned to `docs/api-reference.md` sections 9 and 10; the setup availability probe is coordinated with the Phase 3 plan and recorded in `decisions.md` |
| Component props drift from the written language | props follow design-language section 8: a prop not in that document is added there first, and the doc version bump is recorded in `decisions.md` |

Decisions proposed: D-011, first-load JS <= 150 KB gzip per route and total `web/out` <= 3 MB,
enforced by `scripts/web_check.py` from this phase on, because `docs/performance.md` has no
frontend budget yet and the static export needs a hard ceiling; D-012, theme resolution runs as a
blocking external script `web/public/theme-init.js` instead of the mock's inline script so the CSP
in `docs/security.md` section 8.12 (scripts are not inlined in the export) holds without
`unsafe-inline`.

## Rollback

Phase 4 adds files under `web/`, `tests/ui/` and `scripts/` only; no Python file changes. Deleting
`web/out` (or reverting the Phase 4 commits) makes `layawatch/http/static.py` fall back to the
Phase 0 build-instructions page, so the process always serves something valid. The Playwright
suite sits behind a pytest marker, so `make test` stays green without a browser installed.

## Exit gate

All ten acceptance criteria verified with evidence recorded, the catalog check run against
`docs/design-language.md` section 4, decisions D-011 and D-012 recorded in `decisions.md`, and both
`make test` and `make test-ui` green.
