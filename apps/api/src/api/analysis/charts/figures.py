"""Report figures: timeline, bars, donut, sparklines, and infra-section rows."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Any

from api.analysis.charts.canvas import (
    C_COM,
    C_MIN,
    C_PAR,
    DAMAGE_COLOR,
    FAINT,
    INK,
    LAND,
    MUTED,
    RULE,
    SEA,
    SEV_RANK,
    UNDP,
    contains,
    daily_counts,
    day_range,
    esc,
    svg_open,
)
from api.analysis.metrics import (
    DayCount,
    District,
    InfraItem,
    ReportPoint,
)

# Timeline


def timeline_svg(points: Sequence[ReportPoint], *, w: int = 266, h: int = 108) -> tuple[str, str]:
    """Reports per day by damage class with a cumulative curve. Returns (svg, trend status)."""
    days = day_range(points)
    by = daily_counts(points, days)
    return _timeline_core(days, by, w=w, h=h)


def timeline_from_daily(
    daily: Sequence[DayCount], *, w: int = 266, h: int = 108
) -> tuple[str, str]:
    """`timeline_svg` from SQL-aggregated daily counts."""
    days = [d.day for d in daily]
    by = {
        "minimal": [d.minimal for d in daily],
        "partial": [d.partial for d in daily],
        "complete": [d.complete for d in daily],
    }
    return _timeline_core(days, by, w=w, h=h)


def _timeline_core(
    days: list[date], by: dict[str, list[int]], *, w: int, h: int
) -> tuple[str, str]:
    if not days:
        return svg_open(w, h) + "</svg>", "No dated reports yet."
    totals = [sum(by[c][i] for c in by) for i in range(len(days))]
    # Label only the busiest and quietest days with reports so labels don't collide.
    nz = [i for i, t in enumerate(totals) if t]
    label_idx: set[int] = (
        {max(nz, key=lambda i: totals[i]), min(nz, key=lambda i: totals[i])} if nz else set()
    )
    cum: list[int] = []
    acc = 0
    for t in totals:
        acc += t
        cum.append(acc)
    roll3 = [sum(totals[max(0, i - 2) : i + 1]) for i in range(len(totals))]
    last3, peak3 = roll3[-1], max(roll3)
    status = (
        f"Reporting has plateaued: {last3} reports in the last 3 days vs {peak3} at peak. "
        f"Figures are unlikely to move without new outreach."
        if last3 < 0.2 * peak3
        else f"Reporting is still active: {last3} reports in the last 3 days (peak 3-day window: {peak3})."
    )
    ml, mr, mt, mb = 30, 34, 8, 22
    cw, ch = w - ml - mr, h - mt - mb
    ymax = max(max(totals), 1)
    step = cw / len(days)
    bw = step * 0.62
    parts = [svg_open(w, h)]
    for frac in (0, 0.5, 1.0):
        v = round(ymax * frac)
        y = mt + ch - ch * frac
        parts.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + cw}" y2="{y:.1f}" stroke="{RULE}" stroke-width="0.7"/>'
            f'<text x="{ml - 4}" y="{y + 2.5:.1f}" text-anchor="end" font-size="7.5" fill="{FAINT}">{v}</text>'
        )
    for i, d in enumerate(days):
        x = ml + i * step + (step - bw) / 2
        y = mt + ch
        for cls in ("minimal", "partial", "complete"):
            n = by[cls][i]
            if not n:
                continue
            bh = ch * n / ymax
            y -= bh
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{DAMAGE_COLOR[cls]}"/>'
            )
        if i in label_idx:
            parts.append(
                f'<text x="{x + bw / 2:.1f}" y="{y - 3:.1f}" text-anchor="middle" '
                f'font-size="7" fill="{MUTED}">{totals[i]}</text>'
            )
        # A "%d %b" label needs about 30px, so label every Nth day on long crises.
        label_every = max(1, math.ceil(30.0 / step))
        if i % label_every == 0:
            parts.append(
                f'<text x="{ml + i * step + step / 2:.1f}" y="{h - 8}" text-anchor="middle" '
                f'font-size="7.5" fill="{MUTED}">{d.strftime("%d %b")}</text>'
            )
    cmax = cum[-1] or 1
    pts: list[str] = []
    for i, c in enumerate(cum):
        x = ml + i * step + step / 2
        y = mt + ch - ch * c / cmax
        pts.append(f"{x:.1f},{y:.1f}")
    parts.append(
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{UNDP}" stroke-width="2"/>'
    )
    x_end, y_end = pts[-1].split(",")
    ly = float(y_end) + (12 if float(y_end) < mt + 14 else -5)
    label = str(cum[-1]) if w < 320 else f"{cum[-1]} cumulative"
    parts.append(
        f'<circle cx="{x_end}" cy="{y_end}" r="2.4" fill="{UNDP}"/>'
        f'<text x="{float(x_end) - 5:.1f}" y="{ly:.1f}" text-anchor="end" font-size="8.5" '
        f'font-weight="700" fill="{UNDP}">{label}</text>'
    )
    parts.append("</svg>")
    return "".join(parts), status


# Small charts


def stacked_bars_svg(
    rows: Sequence[tuple[str, Counter[str]]],
    *,
    w: int = 266,
    lbl_w: int = 80,
    bold: frozenset[str] = frozenset(),
    single_color: str | None = None,
) -> str:
    """Horizontal bars stacked three-colour by damage class (or plain bars in
    `single_color`)."""
    row_h, bar_h, top = 21, 11, 6
    h = top + row_h * len(rows) + 4
    mx = max((sum(c.values()) for _, c in rows), default=1) or 1
    val_w = 36
    bw_max = w - lbl_w - val_w
    parts = [svg_open(w, h)]
    for i, (label, counts) in enumerate(rows):
        y = top + i * row_h
        tot = sum(counts.values())
        parts.append(
            f'<text x="{lbl_w - 6}" y="{y + bar_h - 2}" text-anchor="end" font-size="8.5" '
            f'fill="{INK}" font-weight="{700 if label in bold else 400}">{esc(label)}</text>'
        )
        x = float(lbl_w)
        if single_color:
            bw = bw_max * tot / mx
            parts.append(
                f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="{bar_h}" fill="{single_color}"/>'
            )
            x += bw
        else:
            for cls in ("minimal", "partial", "complete"):
                n = counts.get(cls, 0)
                if not n:
                    continue
                bw = bw_max * n / mx
                parts.append(
                    f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="{bar_h}" fill="{DAMAGE_COLOR[cls]}"/>'
                )
                x += bw
        parts.append(
            f'<text x="{x + 5:.1f}" y="{y + bar_h - 2}" font-size="8.5" fill="{INK}" '
            f'font-weight="700">{tot}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


# Distinct hues for the blocked-access donut segments.
_DONUT_PALETTE = ["#b3261e", "#0468b1", "#e0701f", "#0d9488", "#7a3fa0", "#c77c10"]


def debris_donut_svg(rows: Sequence[tuple[str, int]], *, w: int = 266) -> str:
    """Donut of blocked-access reports per top district, total in the hole, legend right."""
    rows = [(name, c) for name, c in rows if c > 0]
    n = len(rows)
    h = max(96, 16 + n * 15)
    cx, cy, rad, ri = 46.0, h / 2.0, 38.0, 21.0
    total = sum(c for _, c in rows)
    parts = [svg_open(w, h)]
    if total <= 0 or n == 0:
        parts.append("</svg>")
        return "".join(parts)

    def seg_color(i: int) -> str:
        return _DONUT_PALETTE[i % len(_DONUT_PALETTE)]

    if n == 1:
        parts.append(
            f'<circle cx="{cx}" cy="{cy:.1f}" r="{rad}" fill="{seg_color(0)}"/>'
            f'<circle cx="{cx}" cy="{cy:.1f}" r="{ri}" fill="{SEA}"/>'
        )
    else:
        ang = -math.pi / 2
        for i, (_, c) in enumerate(rows):
            frac = c / total
            ang1 = ang + frac * 2 * math.pi
            x0, y0 = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
            x1, y1 = cx + rad * math.cos(ang1), cy + rad * math.sin(ang1)
            xi1, yi1 = cx + ri * math.cos(ang1), cy + ri * math.sin(ang1)
            xi0, yi0 = cx + ri * math.cos(ang), cy + ri * math.sin(ang)
            large = 1 if frac > 0.5 else 0
            parts.append(
                f'<path d="M{x0:.1f} {y0:.1f} A{rad} {rad} 0 {large} 1 {x1:.1f} {y1:.1f} '
                f'L{xi1:.1f} {yi1:.1f} A{ri} {ri} 0 {large} 0 {xi0:.1f} {yi0:.1f} Z" '
                f'fill="{seg_color(i)}" stroke="#fff" stroke-width="1"/>'
            )
            ang = ang1
    parts.append(
        f'<text x="{cx}" y="{cy - 1:.1f}" text-anchor="middle" font-size="13" '
        f'font-weight="700" fill="{INK}">{total}</text>'
        f'<text x="{cx}" y="{cy + 9:.1f}" text-anchor="middle" font-size="6" '
        f'letter-spacing="0.6" fill="{MUTED}">BLOCKED</text>'
    )
    lx = 100.0
    ly = (h - n * 15) / 2.0 + 9.0
    for i, (name, c) in enumerate(rows):
        y = ly + i * 15
        nm = name if len(name) <= 20 else name[:19] + "…"
        parts.append(
            f'<rect x="{lx}" y="{y - 7:.1f}" width="8" height="8" rx="1.5" fill="{seg_color(i)}"/>'
            f'<text x="{lx + 13}" y="{y:.1f}" font-size="8.2" fill="{INK}">{esc(nm)}</text>'
            f'<text x="{w - 2}" y="{y:.1f}" text-anchor="end" font-size="8.2" '
            f'font-weight="700" fill="{INK}">{c}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def mix_bar_svg(minimal: int, partial: int, complete: int, *, w: int = 86, h: int = 9) -> str:
    n = minimal + partial + complete
    if not n:
        return f'<svg width="{w}" height="{h}"><rect width="{w}" height="{h}" fill="{LAND}"/></svg>'
    parts = [svg_open(w, h)]
    x = 0.0
    for v, col in ((minimal, C_MIN), (partial, C_PAR), (complete, C_COM)):
        bw = w * v / n
        if bw:
            parts.append(f'<rect x="{x:.1f}" width="{bw:.1f}" height="{h}" fill="{col}"/>')
        x += bw
    parts.append("</svg>")
    return "".join(parts)


def sparkline_svg(
    district_points: Sequence[ReportPoint], days: list[date], *, w: int = 64, h: int = 16
) -> str:
    idx = {d: i for i, d in enumerate(days)}
    counts = [0] * len(days)
    for p in district_points:
        i = idx.get(p.created_at.date())
        if i is not None:
            counts[i] += 1
    return sparkline_counts_svg(counts, w=w, h=h)


def sparkline_counts_svg(counts: Sequence[int], *, w: int = 64, h: int = 16) -> str:
    """`sparkline_svg` from per-day counts aligned to the report's day axis."""
    mx = max(max(counts, default=0), 1)
    n = len(counts)
    step = w / n if n else w
    bw = max(step - 1.1, 1.4)
    parts = [svg_open(w, h)]
    parts.append(
        f'<line x1="0" y1="{h - 0.5}" x2="{w}" y2="{h - 0.5}" stroke="{RULE}" stroke-width="1"/>'
    )
    for i, c in enumerate(counts):
        if not c:
            continue
        bh = max((h - 2) * c / mx, 1.2)
        parts.append(
            f'<rect x="{i * step:.1f}" y="{h - 1 - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{UNDP}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


# Data shaping for the infra section

INFRA_LABEL = {
    "utility": "Utility",
    "community": "Community",
    "transport": "Transport",
    "government": "Government",
    "public_spaces": "Public spaces",
    "health": "Health",
    "education": "Education",
    "water": "Water",
    "power": "Power",
    "telecom": "Telecom",
}

_SECTOR_LABEL = {
    0: "Health",
    1: "Water",
    2: "Power",
    3: "Utility",
    4: "Transport",
    5: "Government",
    6: "Community",
    7: "Other",
}


def infra_damage_rows(items: Sequence[InfraItem]) -> list[tuple[str, Counter[str]]]:
    """Per critical-infra-type damage-class counts, busiest type first."""
    per: dict[str, Counter[str]] = {}
    for it in items:
        per.setdefault(it.infra_type, Counter())[it.damage_class] += 1
    rows = sorted(per.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    return [(INFRA_LABEL.get(t, t.replace("_", " ").title()), c) for t, c in rows]


def _facility_criticality(name: str, types: set[str]) -> int:
    """Lifeline-first ordering: health, water, power, then by type."""
    nm = (name or "").lower()
    if re.search(r"clinic|health|hospital|pharmac", nm):
        return 0
    if "water" in nm or "water" in types:
        return 1
    if re.search(r"transformer|substation|electrical|power", nm) or "power" in types:
        return 2
    for t, rank in (("utility", 3), ("transport", 4), ("government", 5), ("community", 6)):
        if t in types:
            return rank
    return 7


def facility_rows(
    items: Sequence[InfraItem], districts: Sequence[District], *, top: int = 8
) -> list[dict[str, Any]]:
    """One row per named facility across reports, sorted by sector criticality."""
    groups: dict[str, list[InfraItem]] = {}
    for it in items:
        if it.name:
            groups.setdefault(it.name, []).append(it)
    out: list[dict[str, Any]] = []
    for nm, its in groups.items():
        types = {it.infra_type for it in its}
        worst = max(its, key=lambda it: SEV_RANK[it.damage_class]).damage_class
        debris = (
            "Yes"
            if any(it.debris == "yes" for it in its)
            else ("No" if any(it.debris == "no" for it in its) else "n/a")
        )
        report_ids = {it.report_id for it in its}
        lats = [it.lat for it in its if it.lat is not None]
        lons = [it.lon for it in its if it.lon is not None]
        lat = sum(lats) / len(lats) if lats else None
        lon = sum(lons) / len(lons) if lons else None
        district = "n/a"
        if lat is not None and lon is not None:
            for d in districts:
                if contains(d.geom, lon, lat):
                    district = d.name
                    break
        crit = _facility_criticality(nm, types)
        out.append(
            {
                "sector": _SECTOR_LABEL[crit],
                "name": nm,
                "district": district,
                "n": len(report_ids),
                "damage": worst,
                "debris": debris,
                "grid_ref": (
                    f"{lat:.4f}N {lon:.4f}E" if lat is not None and lon is not None else "n/a"
                ),
                "crit": crit,
            }
        )
    out.sort(key=lambda f: (f["crit"], -f["n"], -SEV_RANK[f["damage"]]))
    return out[:top]
