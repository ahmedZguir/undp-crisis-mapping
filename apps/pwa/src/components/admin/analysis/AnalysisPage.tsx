import { type ReactNode, useEffect, useState } from "react";
import { listAdminCrises } from "../../../api/admin";
import { useAdminTimezone } from "../../../hooks/useAdminTimezone";
import { useAnalysisMetrics } from "../../../hooks/useAnalysisMetrics";
import { formatDisplay } from "../../../lib/adminTimezone";
import { STORAGE_KEYS } from "../../../lib/storageKeys";
import type { AdminCrisis, AnalysisDistrict } from "../../../types/admin";
import { CrisisGlyph } from "../crisisIcons";
import { ImpactMap, type ImpactMetric } from "./ImpactMap";
import { formatCountRange, formatUsdRange } from "./format";

// Shared with the dashboard so a crisis picked there carries straight over.
const CRISIS_STORAGE_KEY = STORAGE_KEYS.adminLastCrisis.key;

// Single-crisis by design, with no selector or top-nav tab. The crisis comes
// from the dashboard CTA's hash (`#/analysis?crisis=<uuid>`); a bare hash falls
// back to the last crisis the coordinator was looking at.
function crisisFromHash(): string | null {
  const q = window.location.hash.split("?")[1];
  if (!q) return null;
  return new URLSearchParams(q).get("crisis");
}

function storedCrisis(): string | null {
  try {
    return sessionStorage.getItem(CRISIS_STORAGE_KEY);
  } catch {
    return null;
  }
}

function rememberCrisis(crisisId: string): void {
  try {
    sessionStorage.setItem(CRISIS_STORAGE_KEY, crisisId);
  } catch {
    // sessionStorage unavailable (private mode): non-fatal.
  }
}

const STATUS_PILL: Record<AdminCrisis["status"], { cls: string; label: string }> = {
  active: { cls: "live", label: "live" },
  inactive: { cls: "inactive", label: "inactive" },
  archived: { cls: "archived", label: "archived" },
};

// Coverage values are 0-100 already. Render small-but-nonzero figures honestly.
function coverageLabel(pct: number): string {
  if (pct === 0) return "0%";
  if (pct > 0 && pct < 0.1) return "<0.1%";
  return `${pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
}

function BackArrow() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M15 18l-6-6 6-6" />
    </svg>
  );
}

function InfoGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-4M12 8h.01" />
    </svg>
  );
}

export function AnalysisPage() {
  const tz = useAdminTimezone();
  const [crises, setCrises] = useState<AdminCrisis[] | null>(null);
  const [crisisId] = useState<string | null>(() => crisisFromHash() ?? storedCrisis());
  const { data, loading, error, computedAt, reload } = useAnalysisMetrics(crisisId);
  const [metric, setMetric] = useState<ImpactMetric>("dollars");
  const [selectedDistrictId, setSelectedDistrictId] = useState<string | null>(null);

  // Load the crisis list once, only to resolve the header identity immediately
  // while the heavier metrics compute streams in.
  useEffect(() => {
    let cancelled = false;
    listAdminCrises()
      .then((list) => {
        if (!cancelled) setCrises(list);
      })
      .catch(() => {
        if (!cancelled) setCrises([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (crisisId) rememberCrisis(crisisId);
  }, [crisisId]);

  // Picking a district from the rail jumps to the districts layer and focuses it.
  const pickDistrict = (id: string) => {
    setSelectedDistrictId(id);
    setMetric("districts");
  };

  const selected = crisisId ? (crises?.find((c) => c.id === crisisId) ?? null) : null;
  const crisesLoaded = crises !== null;
  const noCrisis = crisisId == null && crisesLoaded;
  // The chosen crisis no longer exists (deleted), distinct from "no crisis".
  const notFound = crisisId != null && crisesLoaded && !selected && !loading && !data;

  return (
    <div className="dash-root ana-root">
      <div className="page-hero is-compact">
        <div className="page-hero-inner">
          <div className="hero-title" style={{ flex: 1 }}>
            <h1>Analyze Crises</h1>
          </div>
        </div>
      </div>
      <Header
        selected={selected}
        crisisId={crisisId}
        crisesLoaded={crisesLoaded}
        data={data}
        loading={loading}
        computedAt={computedAt}
        tz={tz}
        onReload={reload}
      />

      {noCrisis && (
        <div className="ana-body">
          <EmptyState
            title="No crisis to analyse"
            sub="Open the dashboard, pick a crisis, then choose View full analysis to land here."
          />
        </div>
      )}

      {notFound && (
        <div className="ana-body">
          <EmptyState
            title="Crisis not found"
            sub="It may have been removed. Head back to the dashboard and pick another crisis."
          />
        </div>
      )}

      {crisisId != null && loading && <AnalysisSkeleton name={selected?.name ?? null} />}

      {crisisId != null && error && !loading && (
        <div className="ana-body">
          <ErrorState error={error} onRetry={reload} />
        </div>
      )}

      {crisisId != null && data && !loading && (
        <>
          <KpiRibbon data={data} />
          <div className="ana-work">
            <DistrictsRail
              districts={data.priority_districts}
              selectedId={selectedDistrictId}
              onPick={pickDistrict}
            />
            <div className="ana-main">
              <ImpactMap
                cells={data.cells}
                districts={data.priority_districts}
                bbox={data.meta.bbox}
                geometry={data.meta.geometry}
                metric={metric}
                onMetricChange={(m) => {
                  setMetric(m);
                  // The selected-district outline only belongs on the districts
                  // layer; leaving the AOI clears it.
                  if (m !== "districts") setSelectedDistrictId(null);
                }}
                selectedDistrictId={selectedDistrictId}
                onSelectDistrict={setSelectedDistrictId}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// --- Header band --------------------------------------------------------
// "Computed N ago": these figures are a held snapshot; Recompute fetches fresh ones.
function computedAgoLabel(ts: number): string {
  const secs = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs === 1 ? "1 hour ago" : `${hrs} hours ago`;
}

function Header({
  selected,
  crisisId,
  crisesLoaded,
  data,
  loading,
  computedAt,
  tz,
  onReload,
}: {
  selected: AdminCrisis | null;
  crisisId: string | null;
  crisesLoaded: boolean;
  data: ReturnType<typeof useAnalysisMetrics>["data"];
  loading: boolean;
  computedAt: number | null;
  tz: string;
  onReload: () => void;
}) {
  const backHref = crisisId ? `#/dashboard?crisis=${crisisId}` : "#/dashboard";
  const region = selected?.countries.join(", ") ?? "";
  const pill = selected ? STATUS_PILL[selected.status] : null;
  return (
    <header className="ana-head">
      <a className="ana-back" href={backHref}>
        <BackArrow />
        Dashboard
      </a>

      <span className="ana-crisis">
        <span className="ic">
          <CrisisGlyph type={selected?.type} />
        </span>
        <span className="nm">
          {selected ? (
            <>
              <b title={selected.name}>{selected.name}</b>
              {region && <span className="reg">{region}</span>}
            </>
          ) : crisesLoaded ? (
            <b>Crisis analysis</b>
          ) : (
            <span className="skel skeleton" aria-hidden="true" />
          )}
        </span>
        {pill && (
          <span className={`pill ${pill.cls}`}>
            {selected?.status === "active" && <span className="d" />}
            {pill.label}
          </span>
        )}
        {/* Analysis always runs over the entire crisis, ignoring the
            dashboard's filters and map view; spell that out here. */}
        <span
          className="ana-scope"
          title="Analysis always covers the entire crisis. The filters and map view you set on the dashboard do not apply here."
        >
          Whole crisis
        </span>
      </span>

      <div className="ana-actions">
        {loading ? (
          <span
            className="ana-asof"
            style={{ display: "inline-flex", alignItems: "center", gap: 7 }}
          >
            <span className="spin" style={{ width: 12, height: 12, color: "var(--blue)" }} />
            Computing analysis…
          </span>
        ) : data ? (
          <>
            <span className="ana-asof" title="Timestamp of the most recent report in this crisis">
              <b>Latest report</b> {formatDisplay(data.meta.as_of, tz)} ·{" "}
              <b className="num">{data.meta.report_count.toLocaleString()}</b> reports ·{" "}
              <b className="num">{data.meta.device_count.toLocaleString()}</b> devices
            </span>
            {computedAt != null && (
              <span
                className="ana-asof"
                title="These figures are a cached snapshot. Recompute to rerun against the latest reports."
              >
                Computed {computedAgoLabel(computedAt)}
              </span>
            )}
            <button type="button" className="btn secondary" onClick={onReload}>
              Recompute
            </button>
          </>
        ) : null}
      </div>
    </header>
  );
}

// --- Headline KPI ribbon (dashboard .kpibar, verbatim language) ---------
function tip(h: string, text: string, right = false): ReactNode {
  return (
    <span className={`tip-pop${right ? " right" : ""}`} role="tooltip">
      <span className="tip-h">{h}</span>
      <span className="tip-text">{text}</span>
    </span>
  );
}

function Kpi({
  label,
  children,
  sub,
  tip: tipNode,
}: {
  label: string;
  children: ReactNode;
  sub: ReactNode;
  tip?: ReactNode;
}) {
  return (
    <div className={tipNode ? "kpi has-tip" : "kpi"}>
      <div className="lab">{label}</div>
      <div className="row">{children}</div>
      <div className="sub">{sub}</div>
      {tipNode}
    </div>
  );
}

function KpiRibbon({ data }: { data: NonNullable<ReturnType<typeof useAnalysisMetrics>["data"]> }) {
  const im = data.impact;
  const blindSpots = data.cells.filter((c) => c.blind_spot).length;
  const economic = im.economic_available
    ? formatUsdRange(im.economic_low_usd, im.economic_high_usd)
    : null;
  return (
    <div className="kpibar" aria-label="Headline impact estimates">
      <Kpi
        label="People affected"
        sub={im.population_available ? "modelled range" : "estimated range"}
        tip={tip(
          "People affected",
          im.population_available
            ? "Estimated from how many people live in the damaged areas, scaled by how badly each building was hit. Population data comes from WorldPop."
            : "Roughly 3-6 people per damaged building (population data isn't loaded for this area).",
        )}
      >
        <span className="val num">{formatCountRange(im.displaced_low, im.displaced_high)}</span>
      </Kpi>

      <Kpi
        label="Economic loss"
        sub={economic ? "estimated range" : "LitPop not loaded here"}
        tip={tip(
          "Economic loss",
          "Asset value per building times damage ratios per class. Asset value data comes from LitPop.",
        )}
      >
        {economic ? (
          <span className="val num">{economic}</span>
        ) : (
          <span className="val" style={{ fontSize: 17, color: "var(--muted)" }}>
            Pending
          </span>
        )}
      </Kpi>

      <Kpi
        label="Affected buildings"
        sub="partial damage or worse"
        tip={tip(
          "Affected buildings",
          "Buildings reported at partial or complete damage across the crisis. These drive both the displacement and economic estimates.",
        )}
      >
        <span className="val num">{im.affected_buildings.toLocaleString()}</span>
      </Kpi>

      <Kpi
        label="Coverage"
        sub="of buildings in crisis AOI"
        tip={tip(
          "Coverage",
          "Share of buildings in the crisis AOI with at least one report. Low coverage widens every estimate on this page.",
        )}
      >
        <span className="val num">{coverageLabel(data.meta.coverage_pct)}</span>
      </Kpi>

      <Kpi
        label="Blind-spot cells"
        sub="under-reported + damaged"
        tip={tip(
          "Blind-spot cells",
          "Mesh cells that are both under-reported for their neighbourhood AND embedded in a damaged area. Likely places where damage exists but reports haven't arrived yet.",
        )}
      >
        <span className="val num">{blindSpots.toLocaleString()}</span>
      </Kpi>
    </div>
  );
}

// --- Priority districts rail (left of the map) --------------------------
function severeShare(d: AnalysisDistrict): number {
  const total = d.damage.minimal + d.damage.partial + d.damage.complete;
  return total > 0 ? (d.damage.partial + d.damage.complete) / total : 0;
}

function debrisShare(d: AnalysisDistrict): number {
  return d.debris_known > 0 ? d.debris_yes / d.debris_known : 0;
}

function Meter({ value, color }: { value: number; color: string }) {
  const pct = Math.round(value * 100);
  return (
    <span className="ana-meter">
      <span className="track">
        <span className="fill" style={{ width: `${Math.max(3, pct)}%`, background: color }} />
      </span>
      <span className="pc">{pct}%</span>
    </span>
  );
}

function DistrictCard({
  d,
  active,
  onPick,
}: {
  d: AnalysisDistrict;
  active: boolean;
  onPick: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className={`ana-dcard${active ? " on" : ""}`}
      onClick={() => onPick(d.id)}
      aria-pressed={active}
    >
      <span className="hd">
        <span className="rk">{d.rank}</span>
        <span className="nm">
          {d.name}
          {!d.official && <span className="unmapped"> · unmapped</span>}
        </span>
      </span>
      <span className="rows">
        <span className="mrow">
          <span className="ml">Reports</span>
          <span className="sv num">{d.report_count.toLocaleString()}</span>
        </span>
        <span className="mrow">
          <span className="ml">Severe</span>
          <Meter value={severeShare(d)} color="var(--complete)" />
        </span>
        <span className="mrow">
          <span className="ml">Debris</span>
          {d.debris_known > 0 ? (
            <Meter value={debrisShare(d)} color="var(--partial)" />
          ) : (
            <span className="na">no data</span>
          )}
        </span>
        <span className="mrow">
          <span className="ml">Services hit</span>
          <span className="sv num">{d.services_hit > 0 ? d.services_hit : "—"}</span>
        </span>
      </span>
    </button>
  );
}

function DistrictsRail({
  districts,
  selectedId,
  onPick,
}: {
  districts: AnalysisDistrict[];
  selectedId: string | null;
  onPick: (id: string) => void;
}) {
  return (
    <aside className="ana-side">
      <div className="ana-side-h">
        <div className="t">
          Priority areas
          <span className="ana-defs has-tip" aria-label="What the columns mean">
            <InfoGlyph />
            <span className="tip-pop" role="tooltip">
              <span className="tip-h">How areas are read</span>
              <span className="tip-text">
                <b>Severe</b>: share of the area's reports at partial or complete damage.{" "}
                <b>Debris</b>: share of debris-known reports where the route is blocked.{" "}
                <b>Services hit</b>: damaged or non-functional critical facilities.
              </span>
            </span>
          </span>
        </div>
        <div className="meta">Ranked by severity and reachability. Click one to focus the map.</div>
      </div>

      <div className="ana-side-list">
        {districts.length === 0 ? (
          <div className="ana-empty">
            No areas ranked yet. Reports haven't fallen into a recognised division.
          </div>
        ) : (
          districts.map((d) => (
            <DistrictCard key={d.id} d={d} active={d.id === selectedId} onPick={onPick} />
          ))
        )}
      </div>
    </aside>
  );
}

// --- States -------------------------------------------------------------
// Loading is a skeleton of the real page so the compute reads as "filling in";
// the map panel carries the live "computing" status.
function AnalysisSkeleton({ name }: { name: string | null }) {
  return (
    <>
      <div className="kpibar ana-sk-kpibar" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <div className="kpi" key={i}>
            <div className="skeleton" style={{ width: i % 2 ? 96 : 80, height: 9 }} />
            <div className="skeleton" style={{ width: 66, height: 22, marginTop: 9 }} />
            <div className="skeleton" style={{ width: 110, height: 8, marginTop: 9 }} />
          </div>
        ))}
      </div>

      <div className="ana-work" aria-busy="true">
        <aside className="ana-side">
          <div className="ana-side-h">
            <div className="skeleton" style={{ width: 132, height: 13 }} />
            <div className="skeleton" style={{ width: "78%", height: 9, marginTop: 8 }} />
          </div>
          <div className="ana-side-list">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div className="ana-sk-card" key={i}>
                <div className="r1">
                  <div className="skeleton sq" />
                  <div
                    className="skeleton"
                    style={{ width: `${52 + ((i * 9) % 34)}%`, height: 12 }}
                  />
                </div>
                <div className="skeleton" style={{ width: "100%", height: 8 }} />
                <div className="skeleton" style={{ width: "100%", height: 8 }} />
                <div className="skeleton" style={{ width: "68%", height: 8 }} />
              </div>
            ))}
          </div>
        </aside>

        <div className="ana-main">
          <div className="ana-sk-map">
            <ComputeStatus name={name} />
          </div>
        </div>
      </div>
    </>
  );
}

// The compute is one fetch with no progress events, so the stepper is an
// estimate: stages advance on a timer against a ~1 min budget, the bar eases
// toward (never reaches) full, and the last stage holds until the data lands.
const COMPUTE_STAGES = [
  { key: "reports", label: "Reading reports", weight: 0.16 },
  { key: "buildings", label: "Mapping buildings", weight: 0.24 },
  { key: "damage", label: "Scoring damage", weight: 0.22 },
  { key: "districts", label: "Ranking areas", weight: 0.2 },
  { key: "impact", label: "Estimating impact", weight: 0.18 },
];
const COMPUTE_ESTIMATE_S = 60;

function CheckGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

function ComputeStatus({ name }: { name: string | null }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = performance.now();
    const id = window.setInterval(() => {
      setElapsed((performance.now() - start) / 1000);
    }, 150);
    return () => window.clearInterval(id);
  }, []);

  const frac = elapsed / COMPUTE_ESTIMATE_S;
  // Decelerating fill: quick early, easing toward ~96% so it reads "almost
  // there" without ever claiming completion before the data actually arrives.
  const pct = Math.min(0.96, 1 - Math.exp(-elapsed / 20));

  // Active stage = the first whose cumulative time-share the elapsed fraction
  // hasn't crossed yet; past the budget, hold on the final stage.
  let cum = 0;
  let activeIdx = COMPUTE_STAGES.length - 1;
  for (let i = 0; i < COMPUTE_STAGES.length; i++) {
    cum += COMPUTE_STAGES[i].weight;
    if (frac < cum) {
      activeIdx = i;
      break;
    }
  }

  const remaining = Math.ceil(COMPUTE_ESTIMATE_S - elapsed);
  const eta = remaining > 0 ? `about ${remaining}s remaining` : "wrapping up";

  return (
    <div className="ana-compute" aria-live="polite" aria-busy="true">
      <div className="ana-compute-head">
        <span className="pct num">{Math.round(pct * 100)}%</span>
        <span className="ti">
          <span className="t">Analyzing{name ? ` ${name}` : ""}</span>
          <span className="eta">{eta}</span>
        </span>
      </div>
      <div className="ana-compute-bar">
        <span style={{ width: `${Math.round(pct * 100)}%` }} />
      </div>
      <ol className="ana-compute-steps">
        {COMPUTE_STAGES.map((s, i) => {
          const state = i < activeIdx ? "done" : i === activeIdx ? "active" : "todo";
          return (
            <li key={s.key} className={`cs is-${state}`}>
              <span className="dot">{state === "done" ? <CheckGlyph /> : <i />}</span>
              {s.label}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function ErrorState({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="ana-state is-error">
      <div className="h">Couldn't compute analysis</div>
      <div className="sub">{error}</div>
      <button type="button" className="btn secondary" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

function EmptyState({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="ana-state">
      <div className="h">{title}</div>
      <div className="sub">{sub}</div>
      <a className="btn secondary" href="#/dashboard" style={{ textDecoration: "none" }}>
        Go to dashboard
      </a>
    </div>
  );
}
