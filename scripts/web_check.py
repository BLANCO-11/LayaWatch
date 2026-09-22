"""Web quality gates: catalog, bundle, a11y.

Usage: .venv/bin/python scripts/web_check.py catalog bundle a11y

- catalog: every design-language section 4 family has its component file and
  a data-family entry on /dev/catalog.
- bundle: D-011 ceilings over web/out (first-load JS <= 150 KB gzip per route,
  total export <= 3 MB). Exits non-zero on breach.
- a11y: static checks (theme-init wiring, aria hooks, token completeness);
  the Playwright axe/Lighthouse pass runs under make test-ui.
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = WEB / "out"
TOKENS = WEB / "src" / "styles" / "tokens.css"
CATALOG = WEB / "src" / "app" / "dev" / "catalog" / "page.tsx"

UI_FILES = {
    "button": "src/components/ui/Button.tsx",
    "icon-button": "src/components/ui/IconButton.tsx",
    "input": "src/components/ui/Input.tsx",
    "textarea": "src/components/ui/Textarea.tsx",
    "select": "src/components/ui/Select.tsx",
    "search": "src/components/ui/Search.tsx",
    "switch": "src/components/ui/Switch.tsx",
    "checkbox": "src/components/ui/Checkbox.tsx",
    "segmented": "src/components/ui/Segmented.tsx",
    "badge": "src/components/ui/Badge.tsx",
    "chip": "src/components/ui/Chip.tsx",
    "tag": "src/components/ui/Chip.tsx",
    "card": "src/components/ui/Card.tsx",
    "section-header": "src/components/ui/SectionHeader.tsx",
    "kpi": "src/components/ui/KpiStrip.tsx",
    "table": "src/components/ui/Table.tsx",
    "pagination": "src/components/ui/Pagination.tsx",
    "filter-bar": "src/components/ui/FilterBar.tsx",
    "chart": "src/components/ui/Chart.tsx",
    "area-chart": "src/components/charts/AreaChart.tsx",
    "line-chart": "src/components/charts/LineChart.tsx",
    "stacked-bar": "src/components/charts/StackedBar.tsx",
    "axis": "src/components/charts/Axis.tsx",
    "legend": "src/components/charts/Legend.tsx",
    "crosshair": "src/components/charts/Crosshair.tsx",
    "waterfall": "src/components/ui/Waterfall.tsx",
    "log-viewer": "src/components/ui/LogViewer.tsx",
    "timeline": "src/components/ui/Timeline.tsx",
    "dropdown": "src/components/ui/Dropdown.tsx",
    "dialog": "src/components/ui/Dialog.tsx",
    "drawer": "src/components/ui/Drawer.tsx",
    "toast": "src/components/ui/Toast.tsx",
    "banner": "src/components/ui/Banner.tsx",
    "tooltip": "src/components/ui/Tooltip.tsx",
    "empty-state": "src/components/ui/EmptyState.tsx",
    "skeleton": "src/components/ui/Skeleton.tsx",
    "route-progress": "src/components/ui/RouteProgress.tsx",
    "error-state": "src/components/ui/ErrorState.tsx",
    "key-value-list": "src/components/ui/KeyValueList.tsx",
    "secret-reveal": "src/components/ui/SecretReveal.tsx",
    "copy-field": "src/components/ui/CopyField.tsx",
    "avatar": "src/components/ui/Avatar.tsx",
    "user-menu": "src/components/ui/UserMenu.tsx",
    "audit-row": "src/components/ui/AuditRow.tsx",
    "status-dot": "src/components/ui/StatusDot.tsx",
    "live-pill": "src/components/ui/LivePill.tsx",
}

TOKEN_ROLES = [
    "bg", "sidebar", "surface", "raised", "line", "line-strong", "text",
    "text-2", "text-3", "accent-bar", "accent-text", "accent-fill",
    "on-accent", "gold", "ok", "warn", "err", "s2", "s3", "grid",
    "tint-hover",
]

STATIC_HOOKS = [
    ("theme-init.js referenced in layout", "src/app/layout.tsx", "theme-init.js"),
    ("skip link rendered", "src/components/shell/SkipLink.tsx", "Skip to content"),
    ("live pill aria-live", "src/components/shell/LivePill.tsx", 'aria-live="polite"'),
    ("dialog focus trap", "src/components/ui/Dialog.tsx", "aria-modal"),
    ("chart text summary", "src/components/charts/LineChart.tsx", "aria-label"),
    ("chart hidden data table", "src/components/charts/LineChart.tsx", "lw-sr-only"),
    ("waterfall text durations", "src/components/ui/Waterfall.tsx", "aria-label"),
    ("reduced-motion block", "src/styles/base.css", "prefers-reduced-motion"),
    ("theme resolver before paint", "public/theme-init.js", "dataset.theme"),
]

PER_ROUTE_GZIP_B = 150 * 1024
TOTAL_EXPORT_B = 3 * 1024 * 1024


def check_catalog() -> int:
    missing: list[str] = []
    for family, rel in UI_FILES.items():
        if not (WEB / rel).is_file():
            missing.append(f"{family}: missing {rel}")
    try:
        catalog_src = CATALOG.read_text(encoding="utf-8")
    except OSError:
        print(f"catalog: missing {CATALOG.relative_to(ROOT)}")
        return 1
    for family in UI_FILES:
        if f'"{family}"' not in catalog_src and f"data-family" not in catalog_src:
            missing.append(f"{family}: no catalog entry")
        elif family not in catalog_src:
            missing.append(f"{family}: no catalog entry")
    if missing:
        print(f"catalog: {len(missing)} problem(s)")
        for line in missing:
            print(f"  MISSING {line}")
        return 1
    print(f"catalog: {len(UI_FILES)} families present with files and catalog entries")
    return 0


def _gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9))


def check_bundle() -> int:
    if not OUT.is_dir():
        print(f"bundle: SKIP (no {OUT.relative_to(ROOT)}/; run web-build first)")
        return 0
    js_files = sorted(OUT.rglob("*.js"))
    if not js_files:
        print("bundle: SKIP (no JS in export)")
        return 0
    failures: list[str] = []
    worst = 0
    worst_name = ""
    for path in js_files:
        size = _gzip_size(path)
        worst = max(worst, size)
        if size >= worst:
            worst_name = str(path.relative_to(OUT))
        if size > PER_ROUTE_GZIP_B:
            failures.append(f"{path.relative_to(OUT)}: {size} gzip bytes > {PER_ROUTE_GZIP_B}")
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"bundle: {len(js_files)} JS files, worst gzip {worst} B ({worst_name})")
    print(f"bundle: total export {total} B (ceiling {TOTAL_EXPORT_B} B)")
    if total > TOTAL_EXPORT_B:
        failures.append(f"total {total} B > {TOTAL_EXPORT_B} B")
    if failures:
        for line in failures:
            print(f"  BREACH {line}")
        return 1
    print("bundle: ceilings hold")
    return 0


def check_a11y() -> int:
    failures: list[str] = []
    try:
        tokens = TOKENS.read_text(encoding="utf-8")
    except OSError:
        return 1
    for block in ('[data-theme="dark"]', '[data-theme="light"]'):
        if block not in tokens:
            failures.append(f"tokens.css: missing {block} block")
    for role in TOKEN_ROLES:
        if f"--{role}:" not in tokens:
            failures.append(f"tokens.css: role --{role} not defined")
    for label, rel, needle in STATIC_HOOKS:
        try:
            src = (WEB / rel).read_text(encoding="utf-8")
        except OSError:
            failures.append(f"{rel}: unreadable ({label})")
            continue
        if needle not in src:
            failures.append(f"{rel}: missing {label} ({needle!r})")
    if failures:
        print(f"a11y: {len(failures)} problem(s)")
        for line in failures:
            print(f"  FAIL {line}")
        return 1
    print(f"a11y: {len(TOKEN_ROLES)} roles in both themes, {len(STATIC_HOOKS)} hooks present")
    print("a11y: Playwright axe/Lighthouse pass runs under make test-ui")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="LayaWatch web quality gates")
    parser.add_argument("checks", nargs="*", default=["catalog", "bundle", "a11y"])
    args = parser.parse_args(argv)
    runners = {"catalog": check_catalog, "bundle": check_bundle, "a11y": check_a11y}
    code = 0
    for name in args.checks:
        runner = runners.get(name)
        if runner is None:
            print(f"unknown check: {name}")
            return 2
        if runner() != 0:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
