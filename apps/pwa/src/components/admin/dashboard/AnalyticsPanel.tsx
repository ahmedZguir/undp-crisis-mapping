/**
 * Left analytics panel (`.panel.left`): four charts over the current filtered
 * set, driven entirely by the server-computed `SearchStats`, so they stay
 * accurate when the row payload is truncated. Exact numbers show on hover,
 * with a native `title` as fallback.
 *
 *   1. Reports over time: daily bars stacked by severity + cumulative line
 *   2. Damage distribution: donut by severity
 *   3. Debris blocking access: yes / no / unknown stacked bar
 *   4. Affected infrastructure: horizontal bars per infra type
 */
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import type { DailyBucket, SearchStats } from "../../../api/search";
import { ChartTooltip, TipRow, useChartTooltip } from "./ChartTooltip";

const SEV = {
  complete: "var(--complete)",
  partial: "var(--partial)",
  minimal: "var(--minimal)",
} as const;
const DEBRIS_UNKNOWN = "var(--sev-unknown)";

type SevKey = keyof typeof SEV;
const SEV_ORDER: SevKey[] = ["complete", "partial", "minimal"];

function pct(n: number, d: number): string {
  if (d <= 0) return "0%";
  return `${Math.round((n / d) * 100)}%`;
}

// ── width measuring (for the 1:1, crisp time chart) ──────────────────────
// A callback ref, not useRef + a `[]` layout effect: the measured div mounts
// conditionally (empty state first), and an effect that ran on the empty mount
// would never attach the observer, leaving `w` at 0 and the chart hidden.
function useMeasuredWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState(0);
  const roRef = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    roRef.current?.disconnect();
    if (!el) {
      roRef.current = null;
      return;
    }
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width ?? 0;
      if (cw > 0) setW(cw);
    });
    ro.observe(el);
    roRef.current = ro;
  }, []);
  return [ref, w];
}

// ── section shell ────────────────────────────────────────────────────────
function Section({
  title,
  meta,
  children,
  "data-tour": dataTour,
}: {
  title: string;
  meta?: string;
  children: ReactNode;
  "data-tour"?: string;
}) {
  return (
    <div className="sec" data-tour={dataTour}>
      <div className="sec-h">
        <h3>{title}</h3>
        {meta && <span className="meta">{meta}</span>}
      </div>
      {children}
    </div>
  );
}

// ── 1. Reports over time ───────────────────────────────────────────────────
function fillDays(daily: DailyBucket[]): DailyBucket[] {
  if (daily.length === 0) return [];
  const byDate = new Map(daily.map((d) => [d.date, d]));
  const first = new Date(`${daily[0].date}T00:00:00Z`);
  const last = new Date(`${daily[daily.length - 1].date}T00:00:00Z`);
  const out: DailyBucket[] = [];
  for (let t = first.getTime(); t <= last.getTime(); t += 86_400_000) {
    const iso = new Date(t).toISOString().slice(0, 10);
    out.push(byDate.get(iso) ?? { date: iso, complete: 0, partial: 0, minimal: 0 });
    if (out.length > 120) break; // safety cap on absurd ranges
  }
  return out;
}

function fmtDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

function ReportsOverTime({ daily }: { daily: DailyBucket[] }) {
  const tip = useChartTooltip();
  const [boxRef, w] = useMeasuredWidth();
  const days = fillDays(daily);

  if (days.length === 0) {
    return <div className="sec-empty">No reports in the current view yet.</div>;
  }

  const H = 104;
  const axisTop = 10;
  const axisBottom = 84;
  const left = 27;
  const right = Math.max(left + 10, w - 4);
  const usableH = axisBottom - axisTop;
  const totals = days.map((d) => d.complete + d.partial + d.minimal);
  const maxDay = Math.max(1, ...totals);
  const cum: number[] = [];
  days.reduce((acc, _d, i) => {
    const next = acc + totals[i];
    cum.push(next);
    return next;
  }, 0);
  const maxCum = Math.max(1, cum[cum.length - 1]);
  const band = (right - left) / days.length;
  const barW = Math.max(1.5, band * 0.62);
  const yFor = (v: number) => axisBottom - (v / maxDay) * usableH;
  const yCum = (v: number) => axisBottom - (v / maxCum) * usableH;
  const gridLines = [
    { role: "zero", v: 0 },
    { role: "mid", v: Math.round(maxDay / 2) },
    { role: "top", v: maxDay },
  ];
  const peak = Math.max(...totals);
  const peakDay = days[totals.indexOf(peak)];
  const cx = (i: number) => left + i * band + band / 2;
  const linePts = days.map((_d, i) => `${cx(i).toFixed(1)},${yCum(cum[i]).toFixed(1)}`).join(" ");
  const lastX = cx(days.length - 1);
  const lastY = yCum(cum[cum.length - 1]);

  return (
    <>
      <div className="spark" ref={tip.ref}>
        <div ref={boxRef} style={{ width: "100%" }}>
          {w > 0 && (
            <svg viewBox={`0 0 ${w} ${H}`} role="img" aria-label="Reports per day by severity">
              {/* gridlines + y labels */}
              {gridLines.map((g) => (
                <g key={g.role}>
                  <line
                    x1={left}
                    x2={right}
                    y1={yFor(g.v)}
                    y2={yFor(g.v)}
                    stroke={g.role === "zero" ? "var(--line)" : "var(--line-2)"}
                  />
                  <text
                    x={left - 4}
                    y={yFor(g.v) + 2.5}
                    textAnchor="end"
                    fontSize="7.5"
                    fill="var(--faint)"
                  >
                    {g.v}
                  </text>
                </g>
              ))}
              {/* stacked daily bars */}
              {days.map((d, i) => {
                const x = left + i * band + (band - barW) / 2;
                const hM = (d.minimal / maxDay) * usableH;
                const hP = (d.partial / maxDay) * usableH;
                const hC = (d.complete / maxDay) * usableH;
                const yM = axisBottom - hM;
                const yP = yM - hP;
                const yC = yP - hC;
                return (
                  <g key={d.date}>
                    {d.minimal > 0 && (
                      <rect x={x} y={yM} width={barW} height={hM} fill={SEV.minimal} />
                    )}
                    {d.partial > 0 && (
                      <rect x={x} y={yP} width={barW} height={hP} fill={SEV.partial} />
                    )}
                    {d.complete > 0 && (
                      <rect x={x} y={yC} width={barW} height={hC} fill={SEV.complete} />
                    )}
                  </g>
                );
              })}
              {/* cumulative line + endpoint */}
              <polyline
                points={linePts}
                fill="none"
                stroke="var(--chart-cum)"
                strokeWidth="1.8"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              <circle
                cx={lastX}
                cy={lastY}
                r="2.6"
                fill="var(--chart-cum)"
                stroke="var(--elevated)"
                strokeWidth="1.3"
              />
              {/* running-total value at the endpoint */}
              <text
                x={lastX}
                y={lastY + 12}
                textAnchor="end"
                fontSize="9"
                fontWeight={600}
                fill="var(--chart-cum)"
              >
                {cum[cum.length - 1].toLocaleString()}
              </text>
              {/* x-axis endpoints */}
              <text x={left} y={H - 3} textAnchor="start" fontSize="7.5" fill="var(--faint)">
                {fmtDay(days[0].date)}
              </text>
              <text x={right} y={H - 3} textAnchor="end" fontSize="7.5" fill="var(--faint)">
                {fmtDay(days[days.length - 1].date)}
              </text>
              {/* hover hit-areas, one per day */}
              {days.map((d, i) => (
                <rect
                  key={`hit-${d.date}`}
                  className="bar-hit"
                  x={left + i * band}
                  y={axisTop}
                  width={band}
                  height={axisBottom - axisTop}
                  onMouseEnter={(e) =>
                    tip.show(
                      e.currentTarget,
                      <>
                        <div className="ct-title">
                          {fmtDay(d.date)} · {totals[i].toLocaleString()} total
                        </div>
                        <TipRow color={SEV.complete} name="Complete" n={d.complete} />
                        <TipRow color={SEV.partial} name="Partial" n={d.partial} />
                        <TipRow color={SEV.minimal} name="Minimal" n={d.minimal} />
                        <TipRow color="var(--chart-cum)" name="Running total" n={cum[i]} />
                      </>,
                    )
                  }
                  onMouseLeave={tip.hide}
                >
                  <title>{`${fmtDay(d.date)}: ${totals[i]} reports`}</title>
                </rect>
              ))}
            </svg>
          )}
        </div>
        <ChartTooltip tip={tip.tip} />
      </div>
      <div className="spark-cap">
        Bars are daily reports by severity; the blue line is the running total, now{" "}
        <b>{cum[cum.length - 1].toLocaleString()}</b>. Peaked at <b>{peak.toLocaleString()}</b> on{" "}
        {fmtDay(peakDay.date)}.
      </div>
    </>
  );
}

// ── 2. Damage distribution donut ───────────────────────────────────────────
function DamageDonut({ sev, total }: { sev: Record<SevKey, number>; total: number }) {
  const tip = useChartTooltip();
  const R = 46;
  const C = 2 * Math.PI * R;
  let offset = 0;
  const arcs = SEV_ORDER.map((k) => {
    const frac = total > 0 ? sev[k] / total : 0;
    const dash = frac * C;
    const arc = { k, dash, gap: C - dash, dashoffset: -offset };
    offset += dash;
    return arc;
  });

  return (
    <div className="donut-wrap" ref={tip.ref}>
      <div className="donut-box">
        <svg className="donut-c" viewBox="0 0 120 120" role="img" aria-label="Damage distribution">
          <circle cx="60" cy="60" r={R} fill="none" stroke="var(--line)" strokeWidth="13" />
          <g transform="rotate(-90 60 60)" fill="none" strokeWidth="13">
            {arcs.map((a) => (
              <circle
                key={a.k}
                className="arc"
                cx="60"
                cy="60"
                r={R}
                stroke={SEV[a.k]}
                strokeDasharray={`${a.dash} ${a.gap}`}
                strokeDashoffset={a.dashoffset}
                onMouseEnter={(e) =>
                  tip.show(
                    e.currentTarget,
                    <>
                      <div className="ct-title" style={{ textTransform: "capitalize" }}>
                        {a.k}
                      </div>
                      <TipRow color={SEV[a.k]} name={pct(sev[a.k], total)} n={sev[a.k]} />
                    </>,
                  )
                }
                onMouseLeave={tip.hide}
              >
                <title>{`${a.k}: ${sev[a.k]} (${pct(sev[a.k], total)})`}</title>
              </circle>
            ))}
          </g>
          <text className="n num" x="60" y="62" textAnchor="middle">
            {total.toLocaleString()}
          </text>
          <text className="l" x="60" y="74" textAnchor="middle">
            reports
          </text>
        </svg>
      </div>
      <div className="legend">
        {SEV_ORDER.map((k) => (
          <div className="lr" key={k}>
            <span className="sw" style={{ background: SEV[k] }} />
            <span className="nm">{k}</span>
            <span className="vl num">{sev[k].toLocaleString()}</span>
            <span className="pc num">{pct(sev[k], total)}</span>
          </div>
        ))}
      </div>
      <ChartTooltip tip={tip.tip} />
    </div>
  );
}

// ── 3. Debris blocking access ──────────────────────────────────────────────
function DebrisAccessBar({
  debrisYes,
  debrisKnown,
  total,
}: {
  debrisYes: number;
  debrisKnown: number;
  total: number;
}) {
  const tip = useChartTooltip();
  const no = Math.max(0, debrisKnown - debrisYes);
  const unknown = Math.max(0, total - debrisKnown);
  const rows = [
    { key: "yes", label: "Yes, blocked", n: debrisYes, color: SEV.partial },
    { key: "no", label: "No", n: no, color: SEV.minimal },
    { key: "unknown", label: "Unknown", n: unknown, color: DEBRIS_UNKNOWN },
  ];

  return (
    <div className="fill-col" ref={tip.ref} style={{ position: "relative" }}>
      <div className="stat-lead">
        <span className="n num">{debrisYes.toLocaleString()}</span>
        <span className="t">
          reports flag debris blocking access <b className="num">({pct(debrisYes, total)})</b>
        </span>
      </div>
      <div className="stack">
        {rows.map((r) =>
          r.n > 0 ? (
            <i
              key={r.key}
              style={{ width: pct(r.n, total), background: r.color }}
              onMouseEnter={(e) =>
                tip.show(
                  e.currentTarget,
                  <>
                    <div className="ct-title">{r.label}</div>
                    <TipRow color={r.color} name={pct(r.n, total)} n={r.n} />
                  </>,
                )
              }
              onMouseLeave={tip.hide}
              title={`${r.label}: ${r.n} (${pct(r.n, total)})`}
            />
          ) : null,
        )}
      </div>
      <div className="legend" style={{ gap: 5 }}>
        {rows.map((r) => (
          <div className="lr" key={r.key}>
            <span className="sw" style={{ background: r.color }} />
            <span className="nm" style={{ textTransform: "none" }}>
              {r.label}
            </span>
            <span className="vl num">{r.n.toLocaleString()}</span>
            <span className="pc num">{pct(r.n, total)}</span>
          </div>
        ))}
      </div>
      <ChartTooltip tip={tip.tip} />
    </div>
  );
}

// ── 4. Affected infrastructure ─────────────────────────────────────────────
const INFRA_META: Record<string, { label: string; color: string; d: string }> = {
  residential: {
    label: "Residential",
    color: "#4f74a8",
    d: "M4 10.5 12 4l8 6.5M6 9.5V20h12V9.5M10 20v-5h4v5",
  },
  community: {
    label: "Community",
    color: "#3c8e83",
    d: "M9 8a3 3 0 1 0 0-.01M3 20c0-3.2 2.8-5 6-5s6 1.8 6 5M16 6.5a2.8 2.8 0 0 1 0 5.4",
  },
  transport: {
    label: "Transport & communication",
    color: "#6961a8",
    d: "M8 19h6a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h6",
  },
  commercial: {
    label: "Commercial",
    color: "#9c7b6a",
    d: "M5 8h14l-1.2 12H6.2L5 8zM9 8a3 3 0 0 1 6 0",
  },
  utility: {
    label: "Utility",
    color: "#2d8fa6",
    d: "M12 3c3 4 5 6.5 5 9.5A5 5 0 0 1 7 12.5C7 9.5 9 7 12 3Z",
  },
  government: {
    label: "Government",
    color: "#748299",
    d: "M3 21h18M5 21V10M19 21V10M9 21v-6h6v6M4 10l8-5 8 5",
  },
  public_spaces: {
    label: "Public spaces / recreation",
    color: "#b07399",
    d: "M12 22v-6M12 16a5 5 0 0 0 4.6-7A4 4 0 0 0 12 3a4 4 0 0 0-4.6 6A5 5 0 0 0 12 16Z",
  },
  other: { label: "Other", color: "#9aa4b3", d: "M5 12h.01M12 12h.01M19 12h.01" },
};

function infraMeta(key: string) {
  return INFRA_META[key] ?? { label: key.replace(/_/g, " "), color: "#9aa4b3", d: "M5 12h14" };
}

function InfraBreakdown({
  breakdown,
  total,
}: {
  breakdown: Record<string, number>;
  total: number;
}) {
  const tip = useChartTooltip();
  const rows = Object.entries(breakdown)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  if (rows.length === 0) {
    return <div className="sec-empty">No tagged infrastructure in the current view.</div>;
  }
  const max = Math.max(...rows.map(([, n]) => n));

  return (
    <div className="ibars" ref={tip.ref}>
      {rows.map(([key, n]) => {
        const meta = infraMeta(key);
        return (
          <div
            className="ibar"
            key={key}
            style={{ color: meta.color }}
            onMouseEnter={(e) =>
              tip.show(
                e.currentTarget,
                <>
                  <div className="ct-title">{meta.label}</div>
                  <TipRow color={meta.color} name={`${pct(n, total)} of reports`} n={n} />
                </>,
              )
            }
            onMouseLeave={tip.hide}
            title={`${meta.label}: ${n} (${pct(n, total)} of reports)`}
          >
            <div className="ibar-top">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d={meta.d} />
              </svg>
              <span className="ibar-name">{meta.label}</span>
              <span className="ibar-v num">{n.toLocaleString()}</span>
            </div>
            <div className="ibar-track">
              <i style={{ width: `${Math.max(3, (n / max) * 100)}%` }} />
            </div>
          </div>
        );
      })}
      <ChartTooltip tip={tip.tip} />
    </div>
  );
}

// ── panel ────────────────────────────────────────────────────────────────
export function AnalyticsPanel({
  stats,
  loading,
}: {
  stats: SearchStats | null;
  loading: boolean;
}) {
  // Keep the last non-null stats so the panel doesn't blank out mid-refetch.
  const [shown, setShown] = useState<SearchStats | null>(stats);
  useEffect(() => {
    if (stats) setShown(stats);
  }, [stats]);

  if (!shown) {
    return (
      <div className="panel left" aria-label="Analytics" aria-busy={loading} data-tour="analytics">
        <div className="sec">
          <div className="sec-empty">{loading ? "Loading analytics…" : "No data yet."}</div>
        </div>
      </div>
    );
  }

  const sev: Record<SevKey, number> = {
    complete: shown.severity.complete ?? 0,
    partial: shown.severity.partial ?? 0,
    minimal: shown.severity.minimal ?? 0,
  };

  return (
    // Dim stale charts while the slow whole-set search recomputes, with the same
    // sticky sweep as the KPI ribbon (which carries the worded chip).
    <div
      className="panel left"
      aria-label="Analytics"
      aria-busy={loading}
      data-tour="analytics"
      style={{ opacity: loading ? 0.6 : 1, transition: "opacity 0.2s ease" }}
    >
      {loading && <span className="calc-bar is-sticky" aria-hidden="true" />}
      <Section title="Reports over time" meta="daily by severity">
        <ReportsOverTime daily={shown.daily} />
      </Section>
      <Section title="Damage distribution" meta="by severity">
        <DamageDonut sev={sev} total={shown.total} />
      </Section>
      <Section title="Debris blocking access" meta="by access">
        <DebrisAccessBar
          debrisYes={shown.debris_yes}
          debrisKnown={shown.debris_known}
          total={shown.total}
        />
      </Section>
      <Section title="Affected infrastructure" meta="by type">
        <InfraBreakdown breakdown={shown.infra_breakdown} total={shown.total} />
      </Section>
    </div>
  );
}
