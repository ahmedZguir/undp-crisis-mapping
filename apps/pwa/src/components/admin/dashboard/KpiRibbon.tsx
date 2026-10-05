/**
 * Top KPI ribbon: five count/percentage metrics over the current filtered set
 * (server-computed `SearchStats`, accurate under truncation) plus a crisis-wide
 * "Location data" cell. "Total reports" equals the filter-bar count.
 *
 * Each count cell shows the view value with the whole-crisis total (from the
 * filter-independent `baseline` stats) as a `/ N` denominator.
 */
import { type ReactNode, useEffect, useState } from "react";
import type { SearchStats } from "../../../api/search";

function pct(n: number, d: number): string {
  if (d <= 0) return "—";
  return `${Math.round((n / d) * 100)}%`;
}

// Percentage that stays honest at the low end: a real but tiny share (e.g. 225
// buildings out of 521,831) must not round to a flat "0%". Shows "<0.1%" for
// anything below that, one decimal under 1%, whole numbers above.
function pctFine(n: number, d: number): string {
  if (d <= 0) return "—";
  const p = (n / d) * 100;
  if (p === 0) return "0%";
  if (p < 0.1) return "<0.1%";
  if (p < 1) return `${p.toFixed(1)}%`;
  return `${Math.round(p)}%`;
}

// Always shown once the baseline loads (a whole-crisis view reads `N / N`).
// Clamped up to the view value so a stale baseline can't render
// `view / smaller-total`. Null while the baseline is unloaded.
function crisisDenom(viewVal: number, crisisVal: number | undefined): number | null {
  if (crisisVal == null) return null;
  return Math.max(crisisVal, viewVal);
}

// Up-chevron used by the "+N in the last 24h" delta.
function UpDelta() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m6 15 6-6 6 6" />
    </svg>
  );
}

// Per-cell explainer: one line on what the cell measures, then the shared note
// on the `big / total` reading (identical across cells).
function countTip(header: string, what: ReactNode): ReactNode {
  return (
    <span className="tip-pop" role="tooltip">
      <span className="tip-h">{header}</span>
      <span className="tip-text">
        {what} The <b>big number</b> follows your current view and filters; the <b>/ figure</b> is
        the whole crisis.
      </span>
    </span>
  );
}

const TOTAL_TIP = countTip("Total reports", "Every damage report submitted for this crisis.");
const DEVICES_TIP = countTip(
  "Unique devices",
  "Distinct devices that sent reports, a rough proxy for how many separate people are reporting.",
);
const BUILDINGS_TIP = countTip(
  "Buildings with reports",
  "Distinct buildings flagged by at least one report.",
);
const COMPLETE_TIP = countTip(
  "Complete damage",
  "Reports rating a building as completely destroyed, the most severe damage class.",
);

// Debris is a percentage, so its explainer is worded for the `view %` reading.
// The `vs N% crisis` line is appended only when that figure renders.
function debrisTip(showVs: boolean): ReactNode {
  return (
    <span className="tip-pop" role="tooltip">
      <span className="tip-h">Debris blocking access</span>
      <span className="tip-text">
        Share of reports flagging debris that blocks roads or entrances. The big <b>%</b> follows
        your current view and filters
        {showVs ? (
          <>
            ; <b>vs N% crisis</b> is the whole-crisis share, for comparison.
          </>
        ) : (
          "."
        )}
      </span>
    </span>
  );
}

function Kpi({
  label,
  children,
  sub,
  tip,
}: {
  label: string;
  children: ReactNode;
  sub: ReactNode;
  tip?: ReactNode;
}) {
  return (
    <div className={tip ? "kpi has-tip" : "kpi"}>
      <div className="lab">{label}</div>
      <div className="row">{children}</div>
      <div className="sub">{sub}</div>
      {tip}
    </div>
  );
}

export function KpiRibbon({
  stats,
  baseline,
  loading,
}: {
  stats: SearchStats | null;
  baseline: SearchStats | null;
  loading: boolean;
}) {
  // Hold the last committed stats so the ribbon doesn't blank mid-refetch.
  const [shown, setShown] = useState<SearchStats | null>(stats);
  useEffect(() => {
    if (stats) setShown(stats);
  }, [stats]);
  // Same hold for the crisis baseline (changes rarely; never blank once set
  // until the crisis switches).
  const [base, setBase] = useState<SearchStats | null>(baseline);
  useEffect(() => {
    if (baseline) setBase(baseline);
  }, [baseline]);

  const s = shown;
  const total = s?.total ?? 0;
  const last24 = s?.last_24h ?? 0;
  const complete = s?.severity.complete ?? 0;
  const devices = s?.unique_devices ?? 0;
  const bAffected = s?.buildings_affected ?? 0;
  const bTotal = s?.buildings_total ?? 0;
  const debrisYes = s?.debris_yes ?? 0;
  const debrisPct = pct(debrisYes, total);

  // Whole-crisis denominators (null only while the baseline is unloaded; once
  // loaded the fraction is always shown, clamped: see crisisDenom).
  const dTotal = crisisDenom(total, base?.total);
  const dDevices = crisisDenom(devices, base?.unique_devices);
  const dComplete = crisisDenom(complete, base?.severity.complete);
  const dAffected = crisisDenom(bAffected, base?.buildings_affected);
  // Crisis-wide debris share, for the "vs N% crisis" comparison. Percentages
  // can legitimately run higher in a zoomed view than crisis-wide, so this is
  // a plain comparison (no clamp) shown only when it differs from the view.
  const cDebrisPct = base ? pct(base.debris_yes, base.total) : null;
  // The crisis-wide debris share is shown only when it differs from the view's;
  // the explainer tooltip rides along only in that two-number case.
  const showDebrisVs = cDebrisPct !== null && cDebrisPct !== debrisPct;

  // Crisis-wide location breakdown (a bbox view always excludes position-less
  // reports). Precise = GPS pin or building match; AI-located = geocode of the
  // route text; none = no resolvable position. The three partition the total.
  const crisisTotal = base?.total ?? 0;
  const geocoded = base?.with_geocode ?? 0;
  const noLocation = base?.unmapped ?? 0;
  const precise = Math.max(0, crisisTotal - geocoded - noLocation);

  return (
    // .calc-wrap hosts the sweep + chip OUTSIDE the dimmed bar so the opacity
    // doesn't fade them. The stats come from the slow whole-set search
    // (ai.loading), which lags the fast viewport map count.
    <div className="calc-wrap">
      {loading && (
        <>
          <span className="calc-bar" aria-hidden="true" />
          <output className="calc-chip">
            <span className="d" aria-hidden="true" />
            Calculating…
          </output>
        </>
      )}
      <div
        className="kpibar"
        aria-label="Key metrics"
        aria-busy={loading}
        data-tour="kpi"
        style={{ opacity: loading ? 0.55 : 1, transition: "opacity 0.2s ease" }}
      >
        <Kpi
          label="Total reports"
          tip={TOTAL_TIP}
          sub={
            <>
              +<b className="num">{last24.toLocaleString()}</b> in the last 24 hours
            </>
          }
        >
          <span className="val num">{total.toLocaleString()}</span>
          {dTotal !== null && (
            <span className="val">
              <small>/ {dTotal.toLocaleString()}</small>
            </span>
          )}
          {last24 > 0 && (
            <span className="delta" title="New in the last 24 hours">
              <UpDelta />
              {last24.toLocaleString()}
            </span>
          )}
        </Kpi>

        <Kpi label="Unique devices" tip={DEVICES_TIP} sub={null}>
          <span className="val num">{devices.toLocaleString()}</span>
          {dDevices !== null && (
            <span className="val">
              <small>/ {dDevices.toLocaleString()}</small>
            </span>
          )}
        </Kpi>

        <Kpi
          label="Buildings with reports"
          tip={BUILDINGS_TIP}
          sub={
            <>
              <b className="num">{pctFine(bAffected, bTotal)}</b> of mapped buildings
            </>
          }
        >
          <span className="val num">{bAffected.toLocaleString()}</span>
          {dAffected !== null && (
            <span className="val">
              <small>/ {dAffected.toLocaleString()}</small>
            </span>
          )}
        </Kpi>

        <Kpi
          label="Complete damage"
          tip={COMPLETE_TIP}
          sub={
            <>
              <b className="num">{pct(complete, total)}</b> of reports in view
            </>
          }
        >
          <span className="val num" style={{ color: "var(--complete)" }}>
            {complete.toLocaleString()}
          </span>
          {dComplete !== null && (
            <span className="val">
              <small>/ {dComplete.toLocaleString()}</small>
            </span>
          )}
        </Kpi>

        <Kpi
          label="Debris blocking access"
          tip={debrisTip(showDebrisVs)}
          sub={
            <>
              <b className="num">{debrisYes.toLocaleString()}</b> reports flag debris
            </>
          }
        >
          <span className="val num" style={{ color: "var(--partial)" }}>
            {debrisPct}
          </span>
          {showDebrisVs && (
            <span className="val">
              <small>vs {cDebrisPct} crisis</small>
            </span>
          )}
        </Kpi>

        {/* Location data quality (exact / AI-located / none). Always
          whole-crisis: "no location" reports are excluded from any map view,
          so a view-scoped split would always read zero there. */}
        <div className="kpi loc has-tip">
          <div className="lab">Location data</div>
          {/* Tooltip is a direct child of the cell so it fades in on hovering the
              whole box, like every other KPI cell. Left-anchored so it opens from
              the label rather than overhanging the dashboard's right edge. */}
          <span className="tip-pop" role="tooltip">
            <span className="tip-h">How each report is located</span>
            <span className="tip-row">
              <span className="sw exact" aria-hidden="true" />
              <span>
                <b>Exact</b>: submitted with coordinates (a GPS pin), or matched to a known
                building.
              </span>
            </span>
            <span className="tip-row">
              <span className="sw ai" aria-hidden="true" />
              <span>
                <b>AI-located</b>: no coordinates shared, so the spot was estimated from the written
                directions.
              </span>
            </span>
            <span className="tip-row">
              <span className="sw none" aria-hidden="true" />
              <span>
                <b>No location</b>: could not be placed on the map at all.
              </span>
            </span>
          </span>
          <div className="locbar">
            {precise > 0 && <i className="e" style={{ flex: precise }} />}
            {geocoded > 0 && <i className="a" style={{ flex: geocoded }} />}
            {noLocation > 0 && <i className="n" style={{ flex: noLocation }} />}
          </div>
          <div className="loclegend">
            <span className="it">
              <span className="sw exact" />
              <b className="num">{precise.toLocaleString()}</b> <span className="k">exact</span>
            </span>
            <span className="it">
              <span className="sw ai" />
              <b className="num">{geocoded.toLocaleString()}</b>{" "}
              <span className="k">AI-located</span>
            </span>
            <span className="it">
              <span className="sw none" />
              <b className="num">{noLocation.toLocaleString()}</b>{" "}
              <span className="k">no location</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
