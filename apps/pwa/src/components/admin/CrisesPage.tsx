import "./CrisesPage.css";
import maplibregl from "maplibre-gl";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createAdminCrisis,
  estimateIngestBuildings,
  estimateIngestBuildingsForGeometry,
  fetchCountries,
  fetchCountriesGeometry,
  fetchCrisisGeometry,
  ingestBuildings,
  listAdminCrises,
  listCrisisJobs,
  patchAdminCrisis,
} from "../../api/admin";
import { getForm } from "../../api/adminForms";
import chemIcon from "../../assets/chem.png";
import civilIcon from "../../assets/civil.png";
import conflictIcon from "../../assets/conflict.png";
import eqIcon from "../../assets/eq.png";
import explosIcon from "../../assets/explos.png";
import floodIcon from "../../assets/flood.png";
import hurricIcon from "../../assets/hurric.png";
import landslideIcon from "../../assets/landslide-icon.webp";
import tsunamiIcon from "../../assets/tsunami.png";
import wildfireIcon from "../../assets/wildfire.png";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import {
  formatDisplay,
  localInputToUtcIso,
  shortZoneLabel,
  utcIsoToLocalInput,
} from "../../lib/adminTimezone";
import type { FormSchema } from "../../types";
import type {
  AdminCrisis,
  AdminCrisisCreate,
  AdminCrisisPatch,
  CountryRow,
  CrisisAreaValue,
  CrisisGeometry,
  CrisisGeometryPart,
  CrisisJob,
  CrisisStatus,
  GeoJSONMultiPolygon,
  GeoJSONPolygon,
  IngestEstimate,
  PublicVisibility,
  SearchSelection,
} from "../../types/admin";
import { CountriesMultiselect } from "./CountriesMultiselect";
import { CrisisAreaPicker, bboxFromPolygon } from "./CrisisAreaPicker";
import { FormBuilderPage } from "./FormBuilderPage";
import { CRISIS_PIN_PATH, CRISIS_TYPE_ICONS } from "./crisisIcons";
import { formPageLabel } from "./formBuilder/pageLabels";
import { Icon } from "./icons";
import { GuidedTour } from "./onboarding/GuidedTour";
import { buildCrisesSteps } from "./onboarding/tourContent";

// Shared crisis-type glyphs, drawn at a lighter 1.7 stroke for list cards and tiles.
function CrisisTypeGlyph({ type, size }: { type: string; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={CRISIS_TYPE_ICONS[type] ?? CRISIS_PIN_PATH} />
    </svg>
  );
}

// Never call `fetchCountries()` at import time: an authed fetch before the auth
// bootstrap races AdminApp's refresh and trips refresh-token reuse detection
// (logging the user out on reload). Callers fetch from mount effects instead.

// Map a Search-tab selection to one geometry part, or null when it can't be
// sent (an Overture pick whose polygon is missing from our snapshot; the UI
// flags it so the admin swaps it for an OSM match or removes it).
function partFromSelection(s: SearchSelection): CrisisGeometryPart | null {
  if (s.source === "osm") return { polygon: s.polygon };
  if (s.polygonMissing) return null;
  return { division_id: s.division_id };
}

function geometryFromArea(value: CrisisAreaValue): CrisisGeometry | undefined {
  if (value.mode === "search") {
    const parts = value.selections
      .map(partFromSelection)
      .filter((p): p is CrisisGeometryPart => p !== null);
    if (parts.length === 0) return undefined;
    // A single place is sent as the plain shape, not a one-member `parts` union.
    if (parts.length === 1) return parts[0];
    return { parts };
  }
  if (value.mode === "upload") return { polygon: value.polygon };
  if (value.mode === "draw") return { polygon: value.polygon };
  return undefined;
}

// Client-side footprint of the chosen area, for the create-flow pre-flight
// estimate. Search merges every loaded selection polygon (overcounting is fine,
// the estimate is an overcount anyway). Countries-only returns none here; the
// caller fetches the union separately.
function polygonFromArea(area: CrisisAreaValue): GeoJSONPolygon | GeoJSONMultiPolygon | undefined {
  if (area.mode === "search") return mergeSelectionPolygons(area.selections);
  if (area.mode === "upload" || area.mode === "draw") return area.polygon;
  return undefined;
}

// Flattens loaded selection polygons into one MultiPolygon, or undefined if none
// has loaded. Not a true union (overlaps kept); the real union happens server-side.
function mergeSelectionPolygons(selections: SearchSelection[]): GeoJSONMultiPolygon | undefined {
  const polys: number[][][][] = [];
  for (const s of selections) {
    const poly = s.polygon;
    if (!poly) continue;
    if (poly.type === "Polygon") polys.push(poly.coordinates);
    else for (const member of poly.coordinates) polys.push(member);
  }
  if (polys.length === 0) return undefined;
  return { type: "MultiPolygon", coordinates: polys };
}

// True when the create payload yields a stored polygon: explicit geometry, or
// countries-only with at least one country (the backend unions the countries).
function willHaveGeometry(area: CrisisAreaValue, countries: string[]): boolean {
  if (geometryFromArea(area) !== undefined) return true;
  if (area.mode === "countries-only" && countries.length > 0) return true;
  return false;
}

// Mirrors the citizen-side StepInfraDetails catalogue (label === value). Keep in sync.
interface TypeOption {
  value: string;
  label: string;
  sublabel?: string;
  icon: string;
  iconSize?: number;
}

interface TypeGroup {
  label: string;
  headerColor: string;
  // Selected-tile tint, pre-picked rather than computed via rgba for cross-browser consistency.
  selectedBg: string;
  options: TypeOption[];
}

// Mirrors DEFAULT_FORM_SCHEMA in apps/api/src/api/crises/default_form.py. Keep in sync.
const DEFAULT_FORM_SCHEMA: FormSchema = {
  pages: [
    { kind: "photo_and_damage", enabled: true, locked: true },
    { kind: "location", enabled: true, locked: true },
    { kind: "description", enabled: true, locked: false },
    { kind: "debris", enabled: true, locked: false },
    { kind: "infra_type", enabled: true, locked: false },
    { kind: "crisis_nature", enabled: true, locked: false },
    { kind: "electricity", enabled: false, locked: false },
    { kind: "health_services", enabled: false, locked: false },
    { kind: "pressing_needs", enabled: false, locked: false },
  ],
};

const TYPE_GROUPS: TypeGroup[] = [
  {
    label: "Natural hazards",
    headerColor: "var(--c-ink-3)",
    selectedBg: "var(--c-blue-50)",
    options: [
      { value: "Earthquake", label: "Earthquake", icon: eqIcon, iconSize: 52 },
      { value: "Flood", label: "Flood", icon: floodIcon },
      { value: "Tsunami", label: "Tsunami", icon: tsunamiIcon },
      { value: "Hurricane", label: "Hurricane", sublabel: "Cyclone / Typhoon", icon: hurricIcon },
      { value: "Landslide", label: "Landslide", icon: landslideIcon },
      { value: "Wildfire", label: "Wildfire", icon: wildfireIcon },
    ],
  },
  {
    label: "Industrial & human-made",
    headerColor: "var(--c-ink-3)",
    selectedBg: "var(--c-blue-50)",
    options: [
      { value: "Explosion", label: "Explosion", icon: explosIcon },
      { value: "Chemical incident", label: "Chemical", icon: chemIcon },
      { value: "Conflict", label: "Conflict", icon: conflictIcon },
      { value: "Civil unrest", label: "Civil unrest", icon: civilIcon },
    ],
  },
];

const TYPE_OPTIONS: TypeOption[] = TYPE_GROUPS.flatMap((g) => g.options);

function statusChipClass(status: CrisisStatus): string {
  if (status === "active") return "chip safe";
  if (status === "inactive") return "chip warn";
  return "chip";
}

function statusLabel(status: CrisisStatus): string {
  if (status === "active") return "● live";
  if (status === "inactive") return "○ inactive";
  return "archived";
}

function fmtDay(started: string | null): string {
  if (!started) return "—";
  const ms = Date.now() - new Date(started).getTime();
  const days = Math.max(0, Math.floor(ms / (1000 * 60 * 60 * 24)));
  return `day ${days}`;
}

function jobsAllDone(jobs: CrisisJob[]): boolean {
  if (jobs.length === 0) return true;
  return jobs.every((j) => j.status === "succeeded" || j.status === "failed");
}

// Coarse "~2h 5m" / "~12m" / "~40s" label: ingest ETAs are approximations, not stopwatches.
function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `~${s}s`;
  const mins = Math.round(s / 60);
  if (mins < 60) return `~${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins === 0 ? `~${hours}h` : `~${hours}h ${remMins}m`;
}

// Remaining-time estimate from rows landed vs elapsed time; null until there is
// enough signal. Self-corrects on every poll as the real rate emerges.
function liveEtaSeconds(job: CrisisJob): number | null {
  const count = job.progress_count ?? 0;
  const total = job.progress_total ?? 0;
  if (total <= 0 || count <= 0 || count >= total) return null;
  const elapsedMs = Date.now() - new Date(job.started_at).getTime();
  if (elapsedMs <= 0) return null;
  const rowsPerSec = count / (elapsedMs / 1000);
  if (rowsPerSec <= 0) return null;
  return (total - count) / rowsPerSec;
}

interface CreateFormState {
  name: string;
  type: string;
  countries: string[];
  started_at: string;
  ended_at: string;
  area: CrisisAreaValue;
  ingestOnCreate: boolean;
  // ISO 8601 UTC string when a specific time is picked, null when no schedule
  // or when `activateOnIngestSuccess` is the chosen mode.
  activateAt: string | null;
  activateOnIngestSuccess: boolean;
  publicVisibility: PublicVisibility;
  heatmapKAnonymity: number;
  // Public infra-type whitelist: `null` = show all, non-empty array = strict
  // whitelist. Only meaningful when `publicVisibility` is `buildings` or `full`.
  publicInfraTypes: string[] | null;
  // `null` = platform default (DEFAULT_FORM_SCHEMA); otherwise sent as `form_schema` on create.
  formSchema: FormSchema | null;
}

// Keeps list rows, map labels and the KPI ribbon from overflowing. Also enforced
// server-side on `CrisisCreatePayload.name`.
const CRISIS_NAME_MAX_LEN = 60;

const emptyForm: CreateFormState = {
  name: "",
  type: "Earthquake",
  countries: [],
  started_at: "",
  ended_at: "",
  area: { mode: "empty" },
  ingestOnCreate: false,
  activateAt: null,
  activateOnIngestSuccess: false,
  publicVisibility: "aggregate_view",
  heatmapKAnonymity: 1,
  publicInfraTypes: null,
  formSchema: null,
};

// --- Reusable display primitives ----------------------------------------

// Info icon that shows a plain-language explanation on hover or keyboard focus.
function InfoHint({
  children,
  align = "start",
  label = "More information",
}: {
  children: React.ReactNode;
  // "start" centres the bubble on the icon; "end" anchors it right so it doesn't clip a panel.
  align?: "start" | "end";
  label?: string;
}) {
  // A focusable <span>, not a <button>: SectionBlock nests it inside its collapse
  // <button>, and nested buttons are invalid HTML. CSS :focus-visible shows the tip.
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: the hint is a deliberate keyboard-focusable tooltip trigger (no action); focus reveals the tip via :focus-visible, which keyboard users would otherwise miss.
    <span className="info-hint" role="note" tabIndex={0} aria-label={label}>
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M12 16v-4M12 8h.01" />
      </svg>
      <span className={`info-hint-tip${align === "end" ? " end" : ""}`} role="tooltip">
        {children}
      </span>
    </span>
  );
}

// Card-style section with an accented rail and optional numbered head. The head
// is a collapse toggle; the action slot's buttons are siblings of the toggle, not inside it.
function SectionBlock({
  number,
  title,
  hint,
  description,
  rightSlot,
  accent,
  children,
  innerRef,
  collapsible = true,
  defaultCollapsed = false,
  // Column span inside a 12-col `.crisis-grid`: 12 = full row, 6 = half. Single column below 1100px.
  "data-span": dataSpan,
  dataTour,
}: {
  number?: string;
  title: React.ReactNode;
  hint?: React.ReactNode;
  description?: React.ReactNode;
  rightSlot?: React.ReactNode;
  accent?: "default" | "blue" | "warn" | "danger";
  children: React.ReactNode;
  innerRef?: React.Ref<HTMLDivElement>;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  "data-span"?: 6 | 12;
  dataTour?: string;
}) {
  const accentClass =
    accent === "blue"
      ? " is-accent"
      : accent === "warn"
        ? " is-warn"
        : accent === "danger"
          ? " is-danger"
          : "";
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const expanded = !collapsed;
  const headInner = (
    <>
      {number && <span className="section-num">{number}</span>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="section-title">
          <span>{title}</span>
          {hint && <InfoHint align="start">{hint}</InfoHint>}
        </div>
        {description && <div className="section-sub">{description}</div>}
      </div>
      {collapsible && (
        <span aria-hidden="true" className={`section-chevron${expanded ? " is-open" : ""}`}>
          ▾
        </span>
      )}
    </>
  );
  return (
    <section
      ref={innerRef}
      className={`section-block${accentClass}${collapsed ? " is-collapsed" : ""}`}
      data-span={dataSpan ?? undefined}
      data-tour={dataTour}
    >
      <div className={`section-head${collapsible ? " is-toggle" : ""}`}>
        {collapsible ? (
          <button
            type="button"
            className="section-head-toggle"
            aria-expanded={expanded}
            onClick={() => setCollapsed((v) => !v)}
          >
            {headInner}
          </button>
        ) : (
          <div className="section-head-toggle">{headInner}</div>
        )}
        {rightSlot && <div style={{ flexShrink: 0 }}>{rightSlot}</div>}
      </div>
      {expanded && children}
    </section>
  );
}

// --- Schedule activation helpers ----------------------------------------

function TimezoneHint() {
  // Shows which TZ typed times are read in; warning palette because a wrong-TZ activation does real damage.
  const tz = useAdminTimezone();
  return (
    <span
      className="tz-chip-strong"
      title={`All times you type here are interpreted in ${tz}. Switch this from the topbar clock if you need a different zone.`}
    >
      {shortZoneLabel(tz)} time
    </span>
  );
}

// One-row TZ reminder in the page crumb strip: hint icon, zone code and tooltip.
function CrumbTzChip() {
  const tz = useAdminTimezone();
  return (
    <span
      className="crumb-tz"
      title={`Times entered below are read in ${tz} (${shortZoneLabel(tz)}). Change it from the topbar clock.`}
    >
      <span aria-hidden="true">TZ</span>
      <span style={{ fontWeight: 700 }}>{shortZoneLabel(tz)}</span>
    </span>
  );
}

// Opens the native datetime-local picker on a click anywhere in the input, not
// just the calendar icon. `showPicker()` is guarded for older Safari.
function DatePickerInput({
  id,
  value,
  onChange,
  disabled,
  style,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  style?: React.CSSProperties;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <input
      ref={ref}
      id={id}
      type="datetime-local"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      onClick={() => {
        // The picker refuses disabled inputs, and old Safari would throw.
        if (disabled) return;
        const el = ref.current;
        const showPicker = (el as unknown as { showPicker?: () => void } | null)?.showPicker;
        if (typeof showPicker === "function") {
          try {
            showPicker.call(el);
          } catch {
            // Safari < 16 throws if the input is not focused yet; harmless.
          }
        }
      }}
      style={{ cursor: disabled ? "not-allowed" : "pointer", ...style }}
    />
  );
}

function defaultFutureIsoString(): string {
  // Now + 1h, rounded to the minute: clear of the API's 1-minute future floor.
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setSeconds(0, 0);
  return d.toISOString();
}

type ScheduleMode = "manual" | "at_time" | "on_ingest";

interface ScheduleValue {
  activate_at: string | null;
  activate_on_ingest_success: boolean;
}

function ScheduleActivationField({
  value,
  onChange,
  disabled,
  ingestInScope,
  ingestInScopeReason,
}: {
  value: ScheduleValue;
  onChange: (v: ScheduleValue) => void;
  disabled?: boolean;
  ingestInScope: boolean;
  ingestInScopeReason?: string;
}) {
  const tz = useAdminTimezone();
  const mode: ScheduleMode =
    value.activate_at !== null
      ? "at_time"
      : value.activate_on_ingest_success
        ? "on_ingest"
        : "manual";

  const optionRowStyle = { display: "flex", alignItems: "flex-start", marginBottom: 6 } as const;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: 14,
        borderRadius: 10,
        background: "var(--c-blue-50)",
        border: "1px solid var(--c-blue-200)",
        marginBottom: 4,
      }}
    >
      <label className={`choice ${mode === "manual" ? "selected" : ""}`} style={optionRowStyle}>
        <input
          type="radio"
          name="schedule-mode"
          checked={mode === "manual"}
          disabled={disabled}
          onChange={() => onChange({ activate_at: null, activate_on_ingest_success: false })}
        />
        <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontWeight: 600 }}>I'll activate it myself</span>
          <span style={{ fontSize: 13, color: "var(--c-ink-3)", fontWeight: 400 }}>
            The crisis stays hidden until you press the "Activate" button at the bottom of this
            page. Pick this if you're still preparing.
          </span>
        </span>
      </label>

      <label className={`choice ${mode === "at_time" ? "selected" : ""}`} style={optionRowStyle}>
        <input
          type="radio"
          name="schedule-mode"
          checked={mode === "at_time"}
          disabled={disabled}
          onChange={() =>
            onChange({
              activate_at: defaultFutureIsoString(),
              activate_on_ingest_success: false,
            })
          }
        />
        <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontWeight: 600 }}>Auto-activate at a scheduled time</span>
          <span style={{ fontSize: 13, color: "var(--c-ink-3)", fontWeight: 400 }}>
            The system will publish the crisis at the exact moment you choose below. Useful for
            press-embargoed releases or coordinated launches.
          </span>
        </span>
      </label>
      {mode === "at_time" && (
        <div
          className="field"
          style={{
            marginInlineStart: 28,
            marginBottom: 6,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <DatePickerInput
              value={utcIsoToLocalInput(value.activate_at, tz)}
              onChange={(v) =>
                onChange({
                  activate_at: localInputToUtcIso(v, tz),
                  activate_on_ingest_success: false,
                })
              }
              disabled={disabled}
              style={{ maxWidth: 260 }}
            />
            <TimezoneHint />
          </div>
          <div
            style={{
              fontSize: 13,
              color: "var(--c-warn)",
              fontWeight: 600,
              display: "flex",
              gap: 6,
              alignItems: "flex-start",
              lineHeight: 1.45,
            }}
          >
            <span aria-hidden="true" style={{ flexShrink: 0, lineHeight: 1.45 }}>
              ⚠
            </span>
            <span>
              Fires within ~1 minute of this moment, interpreted in{" "}
              <span style={{ fontWeight: 700, whiteSpace: "nowrap" }}>{tz}</span>. Confirm the
              timezone before saving.
            </span>
          </div>
        </div>
      )}

      <label
        className={`choice ${mode === "on_ingest" ? "selected" : ""}`}
        title={!ingestInScope ? ingestInScopeReason : undefined}
        style={{
          ...optionRowStyle,
          ...(!ingestInScope ? { opacity: 0.55 } : {}),
        }}
      >
        <input
          type="radio"
          name="schedule-mode"
          checked={mode === "on_ingest"}
          disabled={disabled || !ingestInScope}
          onChange={() => onChange({ activate_at: null, activate_on_ingest_success: true })}
        />
        <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontWeight: 600 }}>
            Auto-activate as soon as building data finishes loading
          </span>
          <span style={{ fontSize: 13, color: "var(--c-ink-3)", fontWeight: 400 }}>
            The crisis publishes the instant building footprints are ready, so citizens never see an
            "empty" crisis. Requires that you also schedule a building load.
          </span>
        </span>
      </label>
    </div>
  );
}

function isPresetType(v: string): boolean {
  return TYPE_OPTIONS.some((t) => t.value === v);
}

function TypeGrid({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  // Local state so "Other" stays active after a click even while the value is still "".
  const [otherActive, setOtherActive] = useState<boolean>(value !== "" && !isPresetType(value));

  // Reconcile when the parent value changes from outside (e.g. switching crises).
  useEffect(() => {
    if (isPresetType(value)) setOtherActive(false);
    else if (value !== "") setOtherActive(true);
  }, [value]);

  const showOtherSelected = otherActive || (!isPresetType(value) && value !== "");

  const tileStyle = {
    width: "100%",
    minWidth: 0,
    height: 38,
    display: "flex",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-start",
    gap: 8,
    padding: "5px 10px",
    textAlign: "start",
    borderRadius: 7,
  } as const;

  const tileLabelStyle = (sel: boolean, color?: string) =>
    ({
      fontSize: 13,
      fontWeight: sel ? 700 : 600,
      lineHeight: 1.2,
      minWidth: 0,
      color: color ?? "inherit",
      display: "-webkit-box",
      WebkitBoxOrient: "vertical",
      WebkitLineClamp: 2,
      overflow: "hidden",
      wordBreak: "break-word",
    }) as const;

  const groupEyebrowStyle = (color: string) =>
    ({
      fontSize: 10.5,
      fontWeight: 700,
      color,
      textTransform: "uppercase",
      letterSpacing: "0.1em",
      lineHeight: 1.2,
      marginBottom: 4,
    }) as const;

  const gridStyleFor = (cols: number) =>
    ({
      display: "grid",
      gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
      gap: 6,
    }) as const;

  const renderTileFor = (group: TypeGroup) => (opt: TypeOption) => {
    const selected = !showOtherSelected && value === opt.value;
    const selectedStyle = selected
      ? { borderColor: group.headerColor, background: group.selectedBg }
      : null;
    return (
      <button
        key={opt.value}
        type="button"
        aria-pressed={selected}
        title={opt.sublabel ? `${opt.label}: ${opt.sublabel}` : opt.label}
        className={`choice${selected ? " selected" : ""}`}
        onClick={() => {
          setOtherActive(false);
          onChange(opt.value);
        }}
        style={{ ...tileStyle, ...(selectedStyle ?? {}) }}
      >
        <span
          style={{
            display: "inline-flex",
            flexShrink: 0,
            color: selected ? "var(--c-blue-700)" : "var(--c-ink-3)",
          }}
        >
          <CrisisTypeGlyph type={opt.value} size={24} />
        </span>
        <span style={tileLabelStyle(selected, selected ? group.headerColor : undefined)}>
          {opt.label}
        </span>
      </button>
    );
  };

  // Column counts match each group's tile count (6 natural; 4 presets + Other)
  // so rows fill exactly with no empty cells.
  const renderOtherTile = () => (
    <button
      key="__other__"
      type="button"
      aria-pressed={showOtherSelected}
      title="Other (type a custom crisis label)"
      className={`choice${showOtherSelected ? " selected" : ""}`}
      onClick={() => {
        if (!otherActive) {
          setOtherActive(true);
          if (isPresetType(value)) onChange("");
        }
      }}
      style={tileStyle}
    >
      <span
        aria-hidden="true"
        style={{
          width: 26,
          height: 26,
          flexShrink: 0,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 3,
        }}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            style={{ width: 4, height: 4, borderRadius: "50%", background: "currentColor" }}
          />
        ))}
      </span>
      <span style={tileLabelStyle(showOtherSelected)}>Other</span>
    </button>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {TYPE_GROUPS.map((group, idx) => {
        const isLast = idx === TYPE_GROUPS.length - 1;
        const cols = isLast ? group.options.length + 1 : group.options.length;
        return (
          <div key={group.label} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={groupEyebrowStyle(group.headerColor)}>
              {isLast ? `${group.label} & custom` : group.label}
            </div>
            <div style={gridStyleFor(cols)}>
              {group.options.map(renderTileFor(group))}
              {isLast ? renderOtherTile() : null}
            </div>
          </div>
        );
      })}
      {showOtherSelected && (
        <input
          type="text"
          placeholder="Describe the crisis type"
          value={isPresetType(value) ? "" : value}
          onChange={(e) => onChange(e.target.value)}
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid var(--c-line)",
            borderRadius: 7,
            fontSize: 13.5,
            marginTop: -2,
          }}
        />
      )}
    </div>
  );
}

function RequiredMark() {
  return (
    <span
      style={{
        color: "var(--c-danger)",
        marginInlineStart: 8,
        fontSize: 10.5,
        fontWeight: 700,
        letterSpacing: "0.07em",
        textTransform: "uppercase",
        verticalAlign: "middle",
      }}
    >
      Required
    </span>
  );
}

// Crisis id from `#/crises?crisis=<uuid>`, pre-selected on mount (e.g. returning
// from the community form). Null when absent or malformed.
function selectedCrisisIdFromHash(): string | null {
  const query = window.location.hash.split("?")[1];
  if (!query) return null;
  const id = new URLSearchParams(query).get("crisis");
  return id && /^[0-9a-f-]{36}$/.test(id) ? id : null;
}

type StepKind = "done" | "active" | "pending" | "failed" | "running" | "warn" | "muted";

interface StepState {
  kind: StepKind;
  sub: string;
}

export function CrisesPage({
  onboardingActive = false,
  onOnboardingCrisesClose,
}: {
  /** Driven by the first-run walkthrough orchestrator (AdminApp). When true,
   *  the page opens the chapter-1 guided tour over the create form. */
  onboardingActive?: boolean;
  /** Called when chapter 1 finishes (`true`) or is skipped (`false`), so the
   *  orchestrator can hand off to the dashboard chapter or stop. */
  onOnboardingCrisesClose?: (completed: boolean) => void;
} = {}) {
  const tz = useAdminTimezone();
  const [crises, setCrises] = useState<AdminCrisis[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(() => selectedCrisisIdFromHash());
  const [railQuery, setRailQuery] = useState("");
  const [form, setForm] = useState<CreateFormState>(emptyForm);
  // Page-level so the form builder renders in-flow under the topbar, not as an overlay.
  const [formBuilderOpen, setFormBuilderOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [activatePending, setActivatePending] = useState<string | null>(null);
  const [confirmActivateId, setConfirmActivateId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<CrisisJob[]>([]);
  const [ingestPending, setIngestPending] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await listAdminCrises();
      setCrises(list);
      setListError(null);
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selected = useMemo(
    () => crises.find((c) => c.id === selectedId) ?? null,
    [crises, selectedId],
  );

  // --- First-run walkthrough (chapter 1) ---------------------------------
  // Lives here, not in AdminApp, so the activation step can open a real crisis
  // and point at its live Activate button.
  const [tourOpen, setTourOpen] = useState(false);
  const obStartedRef = useRef(false);
  // The one crisis the walkthrough spotlights, opens and names: the demo crisis
  // ("Kuwait Flood", inactive so the bar shows "Activate"), else any inactive
  // one, else the first. Resolved once so all steps refer to the same crisis.
  const demoCrisis = useMemo(
    () =>
      crises.find((c) => c.name?.toLowerCase().includes("kuwait flood")) ??
      crises.find((c) => c.status === "inactive") ??
      crises[0] ??
      null,
    [crises],
  );
  const clearSelection = useCallback(() => setSelectedId(null), []);
  const selectForActivation = useCallback(() => {
    // No cleanup: the selection persists across detail steps; the "inactive"
    // step clears it when stepping back.
    if (demoCrisis) setSelectedId(demoCrisis.id);
  }, [demoCrisis]);
  const crisesSteps = useMemo(
    () => buildCrisesSteps({ clearSelection, selectForActivation, demoName: demoCrisis?.name }),
    [clearSelection, selectForActivation, demoCrisis],
  );
  const handleTourClose = useCallback(
    (completed: boolean) => {
      setTourOpen(false);
      onOnboardingCrisesClose?.(completed);
    },
    [onOnboardingCrisesClose],
  );
  useEffect(() => {
    if (!onboardingActive) {
      obStartedRef.current = false;
      return;
    }
    if (obStartedRef.current || loading) return;
    obStartedRef.current = true;
    setSelectedId(null);
    setTourOpen(true);
  }, [onboardingActive, loading]);

  const jobsTimer = useRef<number | null>(null);
  const visibleRef = useRef(true);
  useEffect(() => {
    const onVis = () => {
      visibleRef.current = !document.hidden;
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setJobs([]);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      if (!visibleRef.current) {
        jobsTimer.current = window.setTimeout(tick, 3000);
        return;
      }
      try {
        const next = await listCrisisJobs(selectedId);
        if (cancelled) return;
        setJobs(next);
        if (!jobsAllDone(next)) {
          jobsTimer.current = window.setTimeout(tick, 3000);
        }
      } catch {
        // swallow; try again
        jobsTimer.current = window.setTimeout(tick, 3000);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (jobsTimer.current) window.clearTimeout(jobsTimer.current);
      jobsTimer.current = null;
    };
  }, [selectedId]);

  const handleCreate = async () => {
    setSubmitError(null);
    if (!form.name.trim()) {
      setSubmitError("Name is required");
      return;
    }
    if (!form.type.trim()) {
      setSubmitError("Type is required");
      return;
    }
    setSubmitting(true);
    try {
      const payload: AdminCrisisCreate = {
        name: form.name.trim(),
        type: form.type.trim(),
      };
      if (form.countries.length > 0) payload.countries = form.countries;
      if (form.started_at) {
        const iso = localInputToUtcIso(form.started_at, tz);
        if (iso) payload.started_at = iso;
      }
      if (form.ended_at) {
        const iso = localInputToUtcIso(form.ended_at, tz);
        if (iso) payload.ended_at = iso;
      }
      const geometry = geometryFromArea(form.area);
      if (geometry) payload.geometry = geometry;
      if (form.activateAt !== null) payload.activate_at = form.activateAt;
      if (form.activateOnIngestSuccess) payload.activate_on_ingest_success = true;
      else if (form.area.mode === "countries-only" && form.countries.length === 0) {
        setSubmitError("Countries-only mode requires at least one country.");
        setSubmitting(false);
        return;
      }
      if (form.publicVisibility !== "aggregate_view") {
        payload.public_visibility = form.publicVisibility;
      }
      if (form.heatmapKAnonymity !== 1) {
        payload.heatmap_k_anonymity = form.heatmapKAnonymity;
      }
      if (
        infraFilterApplies(form.publicVisibility) &&
        form.publicInfraTypes !== null &&
        form.publicInfraTypes.length > 0
      ) {
        payload.public_infra_types = form.publicInfraTypes;
      }
      if (form.formSchema !== null) payload.form_schema = form.formSchema;
      const created = await createAdminCrisis(payload);
      const shouldIngest = form.ingestOnCreate && willHaveGeometry(form.area, form.countries);
      setForm(emptyForm);
      setCrises((prev) => [created, ...prev]);
      setSelectedId(created.id);
      if (shouldIngest) {
        setIngestError(null);
        setIngestPending(true);
        try {
          await ingestBuildings(created.id);
        } catch (err) {
          setIngestError(err instanceof Error ? err.message : String(err));
        } finally {
          setIngestPending(false);
        }
      }
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleStatusChange = async (id: string, status: CrisisStatus) => {
    setStatusError(null);
    setActivatePending(id);
    try {
      const updated = await patchAdminCrisis(id, { status });
      setCrises((prev) => prev.map((c) => (c.id === id ? updated : c)));
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : String(err));
    } finally {
      setActivatePending(null);
      setConfirmActivateId(null);
    }
  };

  const handleIngest = async () => {
    if (!selected) return;
    setIngestError(null);
    setIngestPending(true);
    try {
      await ingestBuildings(selected.id);
      const next = await listCrisisJobs(selected.id);
      setJobs(next);
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngestPending(false);
    }
  };

  // Applied before status grouping so search composes with the Active/Inactive/Archived split.
  const railSearch = railQuery.trim().toLowerCase();
  const matchedCrises = railSearch
    ? crises.filter((c) => c.name.toLowerCase().includes(railSearch))
    : crises;
  const live = matchedCrises.filter((c) => c.status === "active");
  const inactive = matchedCrises.filter((c) => c.status === "inactive");
  const archived = matchedCrises.filter((c) => c.status === "archived");

  return (
    <div
      className="crises-root"
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        background: "var(--c-bg)",
      }}
    >
      {formBuilderOpen ? (
        <FormBuilderPage
          initialSchema={form.formSchema ?? DEFAULT_FORM_SCHEMA}
          onSave={(schema) => {
            setForm({ ...form, formSchema: schema });
            setFormBuilderOpen(false);
          }}
          onCancel={() => setFormBuilderOpen(false)}
        />
      ) : (
        <>
          <div className="page-hero is-compact">
            <div className="page-hero-inner">
              <div className="hero-title" style={{ flex: 1 }}>
                <h1>Create and Edit Crises</h1>
              </div>
            </div>
          </div>

          {(listError || statusError) && (
            <div style={{ padding: "10px 28px 0" }}>
              {listError && (
                <div
                  className="card"
                  style={{
                    borderColor: "var(--c-danger)",
                    background: "var(--c-danger-bg)",
                    color: "var(--c-danger)",
                    fontSize: 14,
                    marginBottom: 8,
                  }}
                >
                  Failed to load crises: {listError}
                </div>
              )}
              {statusError && (
                <div
                  className="card"
                  style={{
                    borderColor: "var(--c-danger)",
                    background: "var(--c-danger-bg)",
                    color: "var(--c-danger)",
                    fontSize: 14,
                    marginBottom: 8,
                  }}
                >
                  {statusError}
                </div>
              )}
            </div>
          )}

          <div className="crises-shell">
            {/* LEFT: work canvas (create or edit) */}
            <div
              style={{
                overflowY: "auto",
                minWidth: 0,
                display: "flex",
                flexDirection: "column",
                background: "var(--c-bg)",
              }}
            >
              <div style={{ display: selected ? "none" : "contents" }}>
                <CreatePanel
                  form={form}
                  setForm={setForm}
                  submitting={submitting}
                  error={submitError}
                  onCreate={handleCreate}
                  onCustomiseForm={() => setFormBuilderOpen(true)}
                />
              </div>
              {selected && (
                <CrisisDetailPanel
                  crisis={selected}
                  jobs={jobs}
                  ingestPending={ingestPending}
                  ingestError={ingestError}
                  statusPending={activatePending === selected.id}
                  confirmActivate={confirmActivateId === selected.id}
                  onIngest={handleIngest}
                  onStatus={handleStatusChange}
                  onAskActivate={() => setConfirmActivateId(selected.id)}
                  onCancelActivate={() => setConfirmActivateId(null)}
                  onPatched={(c) => setCrises((prev) => prev.map((x) => (x.id === c.id ? c : x)))}
                />
              )}
            </div>

            {/* RIGHT: navigator */}
            <aside className="crises-rail" aria-label="Crises list" data-tour="setup-rail">
              <header className="rail-head">
                <div className="rail-head-row">
                  <span className="rail-title">Crises list</span>
                  <span className="rail-total" title="Total crises in the system">
                    {loading ? "…" : crises.length}
                  </span>
                </div>
                <p className="rail-sub">Pick a crisis to view or edit, or start a new one.</p>
                <button
                  type="button"
                  className="btn primary crises-new-crisis"
                  data-tour="setup-newcrisis"
                  onClick={() => setSelectedId(null)}
                >
                  <Icon.plus width="13" height="13" />
                  New Crisis
                </button>
                <div className="rail-search">
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <path d="m21 21-4.3-4.3" />
                  </svg>
                  <input
                    type="text"
                    value={railQuery}
                    onChange={(e) => setRailQuery(e.target.value)}
                    placeholder="Search crises by name…"
                    aria-label="Search crises by name"
                  />
                  {railQuery && (
                    <button
                      type="button"
                      className="rail-search-clear"
                      aria-label="Clear search"
                      onClick={() => setRailQuery("")}
                    >
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                      >
                        <path d="M6 6l12 12M18 6 6 18" />
                      </svg>
                    </button>
                  )}
                </div>
              </header>
              <div className="rail-body">
                {railSearch && live.length + inactive.length + archived.length === 0 ? (
                  <div
                    style={{
                      fontSize: 12.5,
                      color: "var(--c-ink-3)",
                      padding: "10px 12px",
                      border: "1px dashed var(--c-line)",
                      borderRadius: 8,
                    }}
                  >
                    No crises match “{railQuery.trim()}”.
                  </div>
                ) : (
                  <>
                    <NavigatorSection
                      title="Active"
                      count={live.length}
                      tone="safe"
                      crises={live}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                      jobs={jobs}
                      initiallyOpen
                      forceOpen={railSearch.length > 0}
                    />
                    <NavigatorSection
                      title="Inactive"
                      count={inactive.length}
                      tone="warn"
                      crises={inactive}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                      jobs={jobs}
                      initiallyOpen
                      forceOpen={railSearch.length > 0}
                      tourCardId={demoCrisis?.id}
                      tourCardTour="setup-inactive"
                    />
                    <NavigatorSection
                      title="Archived"
                      count={archived.length}
                      tone="muted"
                      crises={archived}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                      jobs={jobs}
                      initiallyOpen={false}
                      forceOpen={railSearch.length > 0}
                      dim
                    />
                  </>
                )}
              </div>
            </aside>
          </div>
        </>
      )}
      <GuidedTour
        open={tourOpen}
        steps={crisesSteps}
        chapter="Chapter 1 of 2"
        doneLabel="Finish Chapter 1"
        onClose={handleTourClose}
      />
    </div>
  );
}

function NavigatorSection({
  title,
  count,
  tone,
  crises,
  selectedId,
  onSelect,
  jobs,
  initiallyOpen,
  forceOpen,
  dim,
  tourCardId,
  tourCardTour,
}: {
  title: string;
  count: number;
  tone: "safe" | "warn" | "muted";
  crises: AdminCrisis[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  jobs: CrisisJob[];
  initiallyOpen: boolean;
  // A rail search forces the section open so matches in a collapsed group show;
  // the user's own collapse state is kept underneath.
  forceOpen?: boolean;
  dim?: boolean;
  // The card matching `tourCardId` gets the `tourCardTour` data-tour anchor.
  tourCardId?: string;
  tourCardTour?: string;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const isOpen = forceOpen || open;
  const dotColor =
    tone === "safe" ? "var(--c-safe)" : tone === "warn" ? "var(--c-warn)" : "var(--c-ink-4)";
  return (
    <div style={{ marginBottom: 10 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="label-eyebrow"
        style={{
          width: "100%",
          textAlign: "start",
          background: "transparent",
          border: "none",
          padding: "2px 2px 6px",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 10, color: "var(--c-ink-4)", lineHeight: 1 }}>
          {isOpen ? "▾" : "▸"}
        </span>
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: dotColor,
            display: "inline-block",
          }}
        />
        <span>{title}</span>
        <span
          style={{
            marginInlineStart: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--c-ink-3)",
            fontWeight: 600,
            letterSpacing: 0,
          }}
        >
          {count}
        </span>
      </button>
      {isOpen &&
        (crises.length === 0 ? (
          <div
            style={{
              fontSize: 12,
              color: "var(--c-ink-3)",
              padding: "8px 12px",
              border: "1px dashed var(--c-line)",
              borderRadius: 8,
            }}
          >
            None.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {crises.map((c) => (
              <NavigatorCard
                key={c.id}
                crisis={c}
                selected={selectedId === c.id}
                onSelect={() => onSelect(c.id)}
                jobs={selectedId === c.id ? jobs : null}
                dim={dim}
                dataTour={tourCardId && c.id === tourCardId ? tourCardTour : undefined}
              />
            ))}
          </div>
        ))}
    </div>
  );
}

function NavigatorCard({
  crisis,
  selected,
  onSelect,
  jobs,
  dim,
  dataTour,
}: {
  crisis: AdminCrisis;
  selected: boolean;
  onSelect: () => void;
  // `null` when this card is not the currently-selected crisis (no polled
  // jobs available); pass [] for "selected but no jobs yet".
  jobs: CrisisJob[] | null;
  dim?: boolean;
  // Walkthrough anchor, set only on the one crisis the tour spotlights.
  dataTour?: string;
}) {
  const tz = useAdminTimezone();
  const ingestJob = jobs ? jobs.find((j) => j.job_type === "ingest_buildings") : undefined;
  const dot = ingestDot(jobs === null ? "unknown" : ingestJob);
  return (
    <button
      type="button"
      onClick={onSelect}
      title={crisis.name}
      data-tour={dataTour}
      style={{
        background: selected ? "var(--c-blue-50)" : "var(--c-card)",
        border: selected ? "1px solid var(--c-blue-700)" : "1px solid var(--c-line)",
        borderRadius: 9,
        padding: "12px 12px",
        display: "grid",
        gridTemplateColumns: "34px 1fr auto",
        gap: 10,
        alignItems: "center",
        textAlign: "start",
        opacity: dim ? 0.85 : 1,
        cursor: "pointer",
        boxShadow: selected ? "0 0 0 3px var(--c-blue-100)" : "none",
        transition: "border-color 120ms ease, background 120ms ease, box-shadow 120ms ease",
      }}
    >
      <span className={`crises-nav-icon${selected ? " on" : ""}`} aria-hidden="true">
        <CrisisTypeGlyph type={crisis.type} />
      </span>
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: 13.5,
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "var(--c-ink)",
            letterSpacing: "-0.005em",
          }}
        >
          {crisis.name}
        </div>
        <div
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--c-ink-3)",
            marginTop: 4,
            display: "flex",
            alignItems: "center",
            gap: 5,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          <span>{crisis.type}</span>
          <span>·</span>
          <span>{crisis.countries.length > 0 ? crisis.countries.join(",") : "—"}</span>
          <span>·</span>
          <span title={dot.tooltip} style={{ color: dot.color, fontFamily: "inherit" }}>
            {dot.icon}
          </span>
          {crisis.status === "inactive" &&
            (crisis.activate_at || crisis.activate_on_ingest_success) && (
              <span
                className="chip blue"
                style={{
                  fontSize: 11,
                  padding: "2px 8px",
                  marginInlineStart: 4,
                  fontFamily: "inherit",
                }}
                title={
                  crisis.activate_at
                    ? `Scheduled to go live at ${formatDisplay(crisis.activate_at, tz)}`
                    : "Scheduled to go live when ingest finishes"
                }
              >
                {crisis.activate_at ? `⏰ ${formatDisplay(crisis.activate_at, tz)}` : "⏳ ingest"}
              </span>
            )}
        </div>
      </div>
      <div
        style={{
          textAlign: "end",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          alignItems: "end",
        }}
      >
        <span
          className={statusChipClass(crisis.status)}
          style={{ padding: "2px 8px", fontSize: 10.5, fontWeight: 600 }}
        >
          {statusLabel(crisis.status)}
        </span>
        <span className="mono" style={{ fontSize: 10.5, fontWeight: 600, color: "var(--c-ink-3)" }}>
          {fmtDay(crisis.started_at)}
        </span>
      </div>
    </button>
  );
}

// Plain-language label for a job's `job_type` wire value.
function jobTypeLabel(jobType: string): string {
  if (jobType === "ingest_buildings") return "Downloading buildings";
  return jobType;
}

function ingestDot(job: CrisisJob | undefined | "unknown"): {
  icon: string;
  color: string;
  tooltip: string;
} {
  if (job === "unknown")
    return { icon: "○", color: "var(--c-ink-3)", tooltip: "select to inspect ingest" };
  if (!job) return { icon: "○", color: "var(--c-ink-3)", tooltip: "no ingest yet" };
  if (job.status === "running")
    return { icon: "⏳", color: "var(--c-blue-700)", tooltip: "ingest running" };
  if (job.status === "succeeded")
    return { icon: "✓", color: "var(--c-safe)", tooltip: "footprints loaded" };
  if (job.status === "failed")
    return { icon: "!", color: "var(--c-danger)", tooltip: "ingest failed" };
  return { icon: "○", color: "var(--c-ink-3)", tooltip: "no ingest yet" };
}

function CreatePanel({
  form,
  setForm,
  submitting,
  error,
  onCreate,
  onCustomiseForm,
}: {
  form: CreateFormState;
  setForm: (f: CreateFormState) => void;
  submitting: boolean;
  error: string | null;
  onCreate: () => void;
  onCustomiseForm: () => void;
}) {
  const set = <K extends keyof CreateFormState>(k: K, v: CreateFormState[K]) =>
    setForm({ ...form, [k]: v });

  // Confirm step before onCreate when an ingest will fire, so the admin sees the cost warning again.
  const [confirmingCreate, setConfirmingCreate] = useState(false);

  const ingestWillRun = form.ingestOnCreate && willHaveGeometry(form.area, form.countries);
  // Unticking every infra type would emit `[]`, which the server treats as
  // "show all". Block submit; the admin should pick visibility "None" instead.
  const infraTypesAllHidden =
    infraFilterApplies(form.publicVisibility) &&
    form.publicInfraTypes !== null &&
    KNOWN_INFRA_TYPE_VALUES.every((v) => !form.publicInfraTypes?.includes(v));

  // Clear `activateOnIngestSuccess` once no ingest will run; the API rejects it without one.
  useEffect(() => {
    if (!ingestWillRun && form.activateOnIngestSuccess) {
      setForm({ ...form, activateOnIngestSuccess: false });
    }
  }, [ingestWillRun, form, setForm]);

  // No crisis exists yet, so estimate from the area footprint via the crisis-free
  // endpoint. Re-runs as the area changes, including late-resolving polygons.
  const [createEstimate, setCreateEstimate] = useState<IngestEstimate | null>(null);
  const [createEstimatePending, setCreateEstimatePending] = useState(false);
  const [createEstimateError, setCreateEstimateError] = useState<string | null>(null);
  useEffect(() => {
    if (!ingestWillRun) {
      setCreateEstimate(null);
      setCreateEstimateError(null);
      setCreateEstimatePending(false);
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    setCreateEstimatePending(true);
    setCreateEstimateError(null);
    void (async () => {
      try {
        let geom = polygonFromArea(form.area);
        if (!geom && form.area.mode === "countries-only" && form.countries.length > 0) {
          geom =
            (await fetchCountriesGeometry(form.countries, { signal: ctrl.signal })) ?? undefined;
        }
        if (!geom) {
          if (!cancelled) {
            setCreateEstimate(null);
            setCreateEstimatePending(false);
          }
          return;
        }
        const est = await estimateIngestBuildingsForGeometry(geom, { signal: ctrl.signal });
        if (!cancelled) {
          setCreateEstimate(est);
          setCreateEstimatePending(false);
        }
      } catch (err) {
        if (cancelled || (err as { name?: string } | null)?.name === "AbortError") return;
        setCreateEstimate(null);
        setCreateEstimateError(err instanceof Error ? err.message : String(err));
        setCreateEstimatePending(false);
      }
    })();
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [ingestWillRun, form.area, form.countries]);

  const handleCreateClick = () => {
    if (ingestWillRun) {
      setConfirmingCreate(true);
      return;
    }
    onCreate();
  };

  return (
    <>
      <div className="crumb-strip">
        <span className="crumb-mark" aria-hidden="true">
          <Icon.plus width="11" height="11" />
        </span>
        <span>New crisis</span>
        <span style={{ color: "var(--c-ink-4)" }}>›</span>
        <span className="crumb-active">Configure</span>
        <span className="chip warn" style={{ fontSize: 10.5, padding: "2px 8px" }}>
          starts inactive
        </span>
        <span className="crumb-spacer" />
        <CrumbTzChip />
      </div>
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          // Always reserve the scrollbar gutter so the centered crisis-grid
          // doesn't jump left when an expanding section makes content scroll.
          scrollbarGutter: "stable",
          padding: "18px 22px 28px",
          background: "var(--c-bg)",
        }}
      >
        <div className="crisis-grid" data-tour="setup-fields">
          <SectionBlock
            number="1"
            dataTour="setup-name"
            title={
              <>
                Crisis name <RequiredMark />
              </>
            }
            description="A short name citizens see in the crises list."
            data-span={6}
          >
            <div className="field">
              <label htmlFor="crisis-name">Crisis name</label>
              <input
                id="crisis-name"
                type="text"
                placeholder="e.g. Spring 2026 Floods, Doha Earthquake"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                maxLength={CRISIS_NAME_MAX_LEN}
                aria-required="true"
              />
              <div
                style={{
                  marginTop: 4,
                  fontSize: 11,
                  textAlign: "end",
                  color:
                    form.name.length >= CRISIS_NAME_MAX_LEN ? "var(--c-warn)" : "var(--c-ink-3)",
                }}
              >
                {form.name.length}/{CRISIS_NAME_MAX_LEN}
              </div>
            </div>
          </SectionBlock>

          <SectionBlock
            number="2"
            dataTour="setup-type"
            title={
              <>
                Crisis type <RequiredMark />
              </>
            }
            description="Pick the closest match; citizens see this label when they file a report."
            data-span={12}
          >
            <TypeGrid value={form.type} onChange={(v) => set("type", v)} />
          </SectionBlock>

          <SectionBlock
            number="3"
            dataTour="setup-area"
            title="Where it happened"
            defaultCollapsed
            description="The boundary used to fetch building data and scope reports."
            hint={
              <>
                You can pin the area to whole <b>countries</b>, search for a named place,{" "}
                <b>draw</b> a polygon on a map, or <b>upload</b> a GeoJSON file. Whichever method
                you pick becomes the single boundary for this crisis.
              </>
            }
            data-span={12}
          >
            <div className="field" style={{ marginBottom: 14 }}>
              <span
                className="label-eyebrow"
                style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              >
                Countries
                <InfoHint>
                  Tag this crisis with one or more ISO countries. Lets the platform default a report
                  language, filter cross-country dashboards, and resolve the area when no polygon is
                  drawn.
                </InfoHint>
              </span>
              <CountriesMultiselect
                value={form.countries}
                onChange={(next) => set("countries", next)}
                placeholder="Select one or more countries…"
              />
            </div>

            <CrisisAreaPicker
              value={form.area}
              onChange={(next) => set("area", next)}
              countries={form.countries}
            />
          </SectionBlock>

          <SectionBlock
            number="4"
            dataTour="setup-visibility"
            title="What the public sees"
            defaultCollapsed
            description="How citizens viewing the public crisis page see other people's reports. Coordinators always see the full data."
            hint={
              <>
                <b>Hidden</b> shows nothing but the area outline. <b>Aggregate heatmap</b> shows
                neighbourhood density. <b>Building markers</b> drops anonymous pins.{" "}
                <b>Full reports</b> exposes photos and descriptions. Use only with explicit consent.
              </>
            }
            data-span={12}
          >
            <PublicVisibilityEditor
              value={form.publicVisibility}
              onChange={(v) => set("publicVisibility", v)}
              kAnonymity={form.heatmapKAnonymity}
              onKAnonymityChange={(k) => set("heatmapKAnonymity", k)}
              infraTypes={form.publicInfraTypes}
              onInfraTypesChange={(v) => set("publicInfraTypes", v)}
            />
          </SectionBlock>

          {(() => {
            const hasGeometry = willHaveGeometry(form.area, form.countries);
            const needsCountries =
              form.area.mode === "countries-only" && form.countries.length === 0;
            return (
              <SectionBlock
                number="5"
                dataTour="setup-buildings"
                accent={hasGeometry && form.ingestOnCreate ? "danger" : "default"}
                title="Load building shapes now?"
                defaultCollapsed
                description="Downloads the outline of every building inside the crisis area, so citizens can tap a specific one when reporting damage."
                hint={
                  <>
                    Building outlines come from an open map dataset (Overture Maps). The download
                    can take several minutes and uses real server resources, so only run it once the
                    crisis area is final.
                  </>
                }
                data-span={6}
              >
                {!hasGeometry ? (
                  <div
                    style={{
                      padding: "14px 16px",
                      background: "var(--c-line-2)",
                      border: "1px dashed var(--c-line)",
                      borderRadius: 10,
                      color: "var(--c-ink-3)",
                      fontSize: 14,
                      lineHeight: 1.55,
                    }}
                  >
                    {needsCountries
                      ? "Pick at least one country in step 3 to enable building load."
                      : "Pick a crisis area in step 3 (search, draw, upload, or countries-only) to enable building load."}
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    <div className="seg-toggle" aria-label="Load buildings now?">
                      <button
                        type="button"
                        aria-pressed={form.ingestOnCreate}
                        className={form.ingestOnCreate ? "on is-danger" : ""}
                        onClick={() => set("ingestOnCreate", true)}
                      >
                        Yes, load now
                      </button>
                      <button
                        type="button"
                        aria-pressed={!form.ingestOnCreate}
                        className={!form.ingestOnCreate ? "on" : ""}
                        onClick={() => set("ingestOnCreate", false)}
                      >
                        No, I'll run it later
                      </button>
                    </div>
                    {form.ingestOnCreate ? (
                      <div
                        style={{
                          display: "flex",
                          gap: 10,
                          padding: "12px 14px",
                          background: "var(--c-danger-bg)",
                          border: "1px solid var(--c-danger)",
                          borderRadius: 10,
                          color: "var(--c-danger)",
                          fontSize: 14,
                          lineHeight: 1.55,
                        }}
                      >
                        <Icon.alert
                          width="18"
                          height="18"
                          style={{ flexShrink: 0, marginTop: 1 }}
                        />
                        <div>
                          <b>Heavy job.</b> Streaming building footprints inside the crisis area
                          from Overture can take several minutes and consumes significant server
                          resources. We'll ask you to confirm one more time before kicking it off.
                        </div>
                      </div>
                    ) : (
                      <div
                        style={{
                          fontSize: 14,
                          color: "var(--c-ink-3)",
                          lineHeight: 1.55,
                        }}
                      >
                        You can trigger the load any time from the crisis detail page after
                        creation. Citizens can't tap a building until this finishes.
                      </div>
                    )}
                  </div>
                )}
              </SectionBlock>
            );
          })()}

          <SectionBlock
            number="6"
            dataTour="setup-activation"
            title="When should it go live?"
            defaultCollapsed
            description="Going live = citizens see it in their crisis picker and can submit reports."
            hint={
              <>
                Until activation, this crisis is completely hidden from the public. Only
                coordinators see it in this console. You can change this choice any time before the
                activation fires.
              </>
            }
            data-span={6}
          >
            <ScheduleActivationField
              value={{
                activate_at: form.activateAt,
                activate_on_ingest_success: form.activateOnIngestSuccess,
              }}
              onChange={(v) =>
                setForm({
                  ...form,
                  activateAt: v.activate_at,
                  activateOnIngestSuccess: v.activate_on_ingest_success,
                })
              }
              ingestInScope={ingestWillRun}
              ingestInScopeReason="Switch step 5 to 'Yes, load now'. This option only works if a building load is scheduled to run."
            />
          </SectionBlock>

          <SectionBlock
            number="7"
            title="When it happened"
            defaultCollapsed
            description="Used for stats and filters. Leave Ended blank if ongoing."
            data-span={6}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                gap: 14,
              }}
            >
              <div className="field">
                <label
                  htmlFor="crisis-started"
                  style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
                >
                  Started at <TimezoneHint />
                </label>
                <DatePickerInput
                  id="crisis-started"
                  value={form.started_at}
                  onChange={(v) => set("started_at", v)}
                />
              </div>
              <div className="field">
                <label
                  htmlFor="crisis-ended"
                  style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
                >
                  Ended at <TimezoneHint />
                </label>
                <DatePickerInput
                  id="crisis-ended"
                  value={form.ended_at}
                  onChange={(v) => set("ended_at", v)}
                />
              </div>
            </div>
          </SectionBlock>

          <SectionBlock
            number="8"
            dataTour="setup-form"
            title="Community form"
            defaultCollapsed
            description="Customise the questions citizens see when submitting a report."
            data-span={6}
          >
            <button type="button" className="btn secondary" onClick={onCustomiseForm}>
              {form.formSchema !== null ? "Edit form" : "Customise form"}
            </button>
          </SectionBlock>

          {error && (
            <div
              className="card"
              data-span={12}
              style={{
                borderColor: "var(--c-danger)",
                background: "var(--c-danger-bg)",
                color: "var(--c-danger)",
                fontSize: 14,
                marginBottom: 0,
              }}
            >
              {error}
            </div>
          )}
        </div>
      </div>
      <div
        style={{
          padding: "12px 22px",
          borderTop: "1px solid var(--c-line)",
          background: "var(--c-card)",
        }}
      >
        <div
          className="crises-form-actions"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            marginInline: "auto",
            width: "100%",
          }}
        >
          {confirmingCreate && ingestWillRun && (
            <div
              className="card"
              style={{
                borderColor: "var(--c-danger)",
                background: "var(--c-danger-bg)",
                padding: 14,
              }}
            >
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 700,
                  color: "var(--c-danger)",
                  marginBottom: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Icon.alert width="16" height="16" />
                Create crisis and run ingest now?
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 10 }}>
                You've asked to <b>load building footprints right after creating</b>. This is an{" "}
                <b>expensive operation</b>: it streams every footprint inside the crisis bbox from
                the Overture release and can take several minutes. You can still create the crisis
                now and run ingest later from the detail page.
                {form.activateOnIngestSuccess && (
                  <div style={{ marginTop: 6 }}>
                    <b>Note:</b> this crisis will go live automatically as soon as building ingest
                    finishes successfully.
                  </div>
                )}
              </div>
              {createEstimatePending && !createEstimate && (
                <div style={{ fontSize: 14, color: "var(--c-ink-3)", marginBottom: 10 }}>
                  Estimating size &amp; time…
                </div>
              )}
              {createEstimate && (
                <div
                  className="card"
                  style={{
                    background: "var(--c-blue-50)",
                    border: "1px solid var(--c-blue-200)",
                    marginBottom: 10,
                  }}
                >
                  <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
                    ≈ {createEstimate.approx_building_count.toLocaleString()} buildings{" "}
                    <span style={{ fontWeight: 400, color: "var(--c-ink-3)" }}>(upper bound)</span>
                  </div>
                  <div style={{ fontSize: 14, lineHeight: 1.5 }}>
                    Estimated time <b>{formatDuration(createEstimate.est_seconds.total)}</b>:{" "}
                    {formatDuration(createEstimate.est_seconds.download)} downloading +{" "}
                    {formatDuration(createEstimate.est_seconds.load)} importing
                  </div>
                </div>
              )}
              {createEstimateError && (
                <div style={{ fontSize: 13, color: "var(--c-danger)", marginBottom: 10 }}>
                  Couldn't estimate: {createEstimateError}
                </div>
              )}
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="btn secondary"
                  style={{ flex: 1 }}
                  onClick={() => setConfirmingCreate(false)}
                  disabled={submitting}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn secondary"
                  style={{ flex: 1 }}
                  onClick={() => {
                    set("ingestOnCreate", false);
                    setConfirmingCreate(false);
                    onCreate();
                  }}
                  disabled={submitting}
                  title="Create the crisis but skip the ingest, you can run it later"
                >
                  Create without ingest
                </button>
                <button
                  type="button"
                  className="btn danger"
                  style={{ flex: 2 }}
                  onClick={() => {
                    setConfirmingCreate(false);
                    onCreate();
                  }}
                  disabled={submitting || infraTypesAllHidden}
                  title={
                    infraTypesAllHidden
                      ? "Re-tick at least one infra type, or set Public visibility to 'None'."
                      : undefined
                  }
                >
                  {submitting ? <span className="spin" /> : null}
                  {submitting ? "Creating…" : "Create & run ingest"}
                </button>
              </div>
            </div>
          )}
          {!confirmingCreate && (
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="btn secondary"
                style={{ flex: 1 }}
                onClick={() => setForm(emptyForm)}
                disabled={submitting}
              >
                Reset
              </button>
              <button
                type="button"
                className="btn"
                style={{ flex: 2 }}
                data-tour="setup-create"
                onClick={handleCreateClick}
                disabled={
                  submitting || !form.name.trim() || !form.type.trim() || infraTypesAllHidden
                }
                title={
                  !form.name.trim()
                    ? "Crisis name is required"
                    : !form.type.trim()
                      ? "Crisis type is required"
                      : infraTypesAllHidden
                        ? "Re-tick at least one infra type, or set Public visibility to 'None'."
                        : undefined
                }
              >
                {submitting ? <span className="spin" /> : null}
                {submitting ? "Creating…" : "Create crisis"}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// Read-only map of a saved crisis polygon. With `pmtilesUrl`, ingested building
// footprints render as a translucent underlay so coverage is visible.
function SavedAreaMap({
  geometry,
  height,
  pmtilesUrl,
}: {
  geometry: GeoJSONPolygon | GeoJSONMultiPolygon;
  height: number;
  pmtilesUrl?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const mapLoadedRef = useRef(false);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          },
        },
        layers: [{ id: "osm-tiles", type: "raster", source: "osm" }],
      },
      center: [0, 20],
      zoom: 1,
      attributionControl: false,
      interactive: true,
    });
    mapRef.current = map;
    map.on("load", () => {
      mapLoadedRef.current = true;
      map.addSource("area", {
        type: "geojson",
        data: { type: "Feature", properties: {}, geometry },
      });
      map.addLayer({
        id: "area-fill",
        type: "fill",
        source: "area",
        paint: { "fill-color": "#1670c2", "fill-opacity": 0.16 },
      });
      map.addLayer({
        id: "area-line",
        type: "line",
        source: "area",
        paint: { "line-color": "#0b3d6e", "line-width": 2.5 },
      });
      const [w, s, e, n] = bboxFromPolygon(geometry);
      map.fitBounds(
        [
          [w, s],
          [e, n],
        ],
        { padding: 18, animate: false, maxZoom: 12 },
      );
    });
    return () => {
      mapLoadedRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, [geometry]);

  // Separate from the area-polygon effect so pmtiles changes don't rebuild the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer("bldg-fill")) map.removeLayer("bldg-fill");
      if (map.getLayer("bldg-line")) map.removeLayer("bldg-line");
      if (map.getSource("bldgs")) map.removeSource("bldgs");
      if (!pmtilesUrl) return;
      map.addSource("bldgs", { type: "vector", url: `pmtiles://${pmtilesUrl}` });
      // Beneath the area outline so buildings never obscure the boundary.
      const before = map.getLayer("area-line") ? "area-line" : undefined;
      map.addLayer(
        {
          id: "bldg-fill",
          type: "fill",
          source: "bldgs",
          "source-layer": "building",
          paint: { "fill-color": "#0468b1", "fill-opacity": 0.35 },
        },
        before,
      );
      map.addLayer(
        {
          id: "bldg-line",
          type: "line",
          source: "bldgs",
          "source-layer": "building",
          minzoom: 14,
          paint: { "line-color": "#0468b1", "line-width": 0.6, "line-opacity": 0.55 },
        },
        before,
      );
    };
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [pmtilesUrl]);

  // Container can mount 0×0 if the parent layout settles late.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => mapRef.current?.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      style={{
        height,
        borderRadius: 8,
        border: "1px solid var(--c-line)",
        overflow: "hidden",
        position: "relative",
      }}
    >
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      {pmtilesUrl && (
        <div
          style={{
            position: "absolute",
            bottom: 10,
            insetInlineStart: 10,
            padding: "5px 10px",
            background: "rgba(13, 17, 23, 0.78)",
            color: "#fff",
            borderRadius: 999,
            fontSize: 12,
            fontWeight: 600,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            pointerEvents: "none",
          }}
        >
          <span
            aria-hidden="true"
            style={{ width: 8, height: 8, borderRadius: 2, background: "#cc5666" }}
          />
          buildings loaded
        </div>
      )}
    </div>
  );
}

// Setup row: shows `display` until Edit is clicked, so a saved crisis reads as a
// record, not a fresh form. The section's Save Changes still commits.
function FieldRow({
  label,
  editing,
  onEdit,
  display,
  editor,
  wide,
}: {
  label: string;
  editing: boolean;
  onEdit: () => void;
  display: React.ReactNode;
  editor: React.ReactNode;
  // `wide` keeps the row vertical in display state, for tall values like map previews.
  wide?: boolean;
}) {
  if (editing) {
    // Edit mode stacks so the inline editor gets the full row width.
    return (
      <div className="field" style={{ marginBottom: 14 }}>
        <span className="label-eyebrow" style={{ marginBottom: 6, display: "block" }}>
          {label}
        </span>
        {editor}
      </div>
    );
  }
  if (wide) {
    // Tall display content: label/edit on top, display full width below.
    return (
      <div style={{ padding: "10px 0", borderBottom: "1px solid var(--c-line-2)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 8,
            gap: 6,
          }}
        >
          <span className="label-eyebrow">{label}</span>
          <button
            type="button"
            onClick={onEdit}
            style={{
              background: "transparent",
              border: "1px solid var(--c-line)",
              borderRadius: 6,
              padding: "5px 11px",
              fontSize: 12.5,
              fontWeight: 600,
              color: "var(--c-blue-700)",
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <Icon.edit width="12" height="12" />
            Edit
          </button>
        </div>
        {display}
      </div>
    );
  }
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(120px, 140px) minmax(0, 1fr) auto",
        alignItems: "center",
        gap: 14,
        padding: "10px 0",
        borderBottom: "1px solid var(--c-line-2)",
      }}
    >
      <span className="label-eyebrow" style={{ alignSelf: "center", lineHeight: 1.4 }}>
        {label}
      </span>
      <div style={{ minWidth: 0, color: "var(--c-ink)", fontSize: 14, lineHeight: 1.4 }}>
        {display}
      </div>
      <button
        type="button"
        onClick={onEdit}
        style={{
          background: "transparent",
          border: "1px solid var(--c-line)",
          borderRadius: 6,
          padding: "5px 11px",
          fontSize: 12.5,
          fontWeight: 600,
          color: "var(--c-blue-700)",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
        }}
      >
        <Icon.edit width="12" height="12" />
        Edit
      </button>
    </div>
  );
}

// Tooltip copy is fixed product wording; change it deliberately.
interface VisibilityOption {
  value: PublicVisibility;
  label: string;
  short: string;
  description: string;
}

const VISIBILITY_OPTIONS: readonly VisibilityOption[] = [
  {
    value: "none",
    label: "Hidden",
    short: "Citizens see only the crisis name and area outline. No reports at all.",
    description:
      "Strongest privacy. Pick this when reports contain sensitive information that should never be exposed.",
  },
  {
    value: "aggregate_view",
    label: "Aggregate heatmap",
    short: "Coloured neighbourhood cells with report counts. No buildings, no photos.",
    description:
      "Citizens see how concentrated reports are across an area, never a specific household.",
  },
  {
    value: "buildings",
    label: "Building markers",
    short: "One anonymous marker per damaged building. Damage class only, no photos.",
    description:
      "Useful when communities benefit from seeing the spatial extent of damage but you still protect individual narratives.",
  },
  {
    value: "full",
    label: "Full reports",
    short: "Everything: photos, descriptions, location, damage class. Strongest transparency.",
    description:
      "Use only when affected residents have explicitly consented to public exposure of their reports.",
  },
];

// Glyph previewing each visibility mode; currentColor makes it follow dark mode.
function VisibilityPreview({ value }: { value: PublicVisibility }) {
  const stroke = "currentColor";
  if (value === "none") {
    return (
      <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden="true">
        <rect
          x="2"
          y="2"
          width="52"
          height="36"
          rx="4"
          fill="none"
          stroke={stroke}
          strokeWidth="1"
          strokeDasharray="3 3"
          opacity="0.6"
        />
      </svg>
    );
  }
  if (value === "aggregate_view") {
    return (
      <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden="true">
        {[
          { x: 10, y: 14, o: 0.25 },
          { x: 22, y: 8, o: 0.55 },
          { x: 22, y: 20, o: 0.8 },
          { x: 34, y: 14, o: 0.4 },
          { x: 34, y: 26, o: 0.6 },
        ].map((c) => (
          <polygon
            key={`${c.x}-${c.y}`}
            points={`${c.x},${c.y - 4} ${c.x + 4},${c.y - 2} ${c.x + 4},${c.y + 2} ${c.x},${c.y + 4} ${c.x - 4},${c.y + 2} ${c.x - 4},${c.y - 2}`}
            fill={stroke}
            opacity={c.o}
          />
        ))}
      </svg>
    );
  }
  if (value === "buildings") {
    return (
      <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden="true">
        {[
          [12, 12],
          [22, 24],
          [34, 14],
          [42, 26],
          [18, 30],
        ].map(([x, y]) => (
          <circle key={`${x}-${y}`} cx={x} cy={y} r="3" fill={stroke} />
        ))}
      </svg>
    );
  }
  return (
    <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden="true">
      <rect x="6" y="8" width="14" height="10" rx="1" fill={stroke} opacity="0.85" />
      <rect x="6" y="20" width="14" height="2" rx="1" fill={stroke} opacity="0.6" />
      <rect x="6" y="24" width="10" height="2" rx="1" fill={stroke} opacity="0.5" />
      <rect x="24" y="8" width="14" height="10" rx="1" fill={stroke} opacity="0.85" />
      <rect x="24" y="20" width="14" height="2" rx="1" fill={stroke} opacity="0.6" />
      <rect x="24" y="24" width="10" height="2" rx="1" fill={stroke} opacity="0.5" />
    </svg>
  );
}

function visibilityLabel(value: PublicVisibility): string {
  return VISIBILITY_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

function PublicVisibilityDisplay({
  value,
  kAnonymity,
  infraTypes,
}: {
  value: PublicVisibility;
  kAnonymity: number;
  infraTypes: string[] | null;
}) {
  const hiddenInfra = infraFilterApplies(value) ? blacklistFromWhitelist(infraTypes) : [];
  const opt = VISIBILITY_OPTIONS.find((o) => o.value === value);
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "60px minmax(0, 1fr)",
        gap: 14,
        alignItems: "center",
        padding: "12px 14px",
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 8,
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: 60,
          height: 44,
          borderRadius: 6,
          background: "var(--c-blue-50)",
          color: "var(--c-blue-700)",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
        }}
      >
        <VisibilityPreview value={value} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontWeight: 600, fontSize: 15 }}>{visibilityLabel(value)}</span>
          {value === "aggregate_view" && (
            <span
              className="mono"
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: "var(--c-blue-700)",
                background: "var(--c-blue-50)",
                border: "1px solid var(--c-blue-200)",
                padding: "1px 8px",
                borderRadius: 999,
              }}
              title="k-anonymity floor: hex cells with fewer reports than this are dropped"
            >
              k ≥ {kAnonymity}
            </span>
          )}
        </div>
        <div style={{ fontSize: 13.5, color: "var(--c-ink-3)", lineHeight: 1.45 }}>
          {opt?.short ?? ""}
        </div>
        {infraFilterApplies(value) && hiddenInfra.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 6,
              alignItems: "center",
              marginTop: 2,
            }}
          >
            <span style={{ fontSize: 12, color: "var(--c-ink-3)" }}>Hidden categories:</span>
            {hiddenInfra.map((v) => (
              <span
                key={v}
                className="chip"
                style={{
                  background: "var(--c-warn-bg)",
                  borderColor: "var(--c-warn)",
                  fontSize: 12,
                  padding: "2px 8px",
                }}
              >
                {infraTypeLabel(v)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PublicVisibilityEditor({
  value,
  onChange,
  kAnonymity,
  onKAnonymityChange,
  infraTypes,
  onInfraTypesChange,
}: {
  value: PublicVisibility;
  onChange: (v: PublicVisibility) => void;
  kAnonymity: number;
  onKAnonymityChange: (k: number) => void;
  infraTypes: string[] | null;
  onInfraTypesChange: (v: string[] | null) => void;
}) {
  const showLowKWarning = value === "aggregate_view" && kAnonymity < 5;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {VISIBILITY_OPTIONS.map((opt) => {
        const selected = value === opt.value;
        const showKAnonymity = selected && opt.value === "aggregate_view";
        const showInfraTypes = selected && (opt.value === "buildings" || opt.value === "full");
        const showInlinePanel = showKAnonymity || showInfraTypes;
        return (
          <Fragment key={opt.value}>
            <label
              htmlFor={`pv-${opt.value}`}
              title={opt.description}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 60px minmax(0, 1fr) auto",
                alignItems: "center",
                gap: 12,
                padding: "12px 14px",
                border: `1px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
                background: selected ? "var(--c-blue-50)" : "var(--c-card)",
                borderRadius: showInlinePanel ? "8px 8px 0 0" : 8,
                borderBottom: showInlinePanel ? "none" : undefined,
                boxShadow: selected ? "none" : "0 1px 0 rgba(13, 17, 23, 0.04)",
                cursor: "pointer",
                transition: "border-color 120ms ease, background 120ms ease",
              }}
            >
              <input
                id={`pv-${opt.value}`}
                type="radio"
                name="public_visibility"
                value={opt.value}
                checked={selected}
                onChange={() => onChange(opt.value)}
              />
              <div
                aria-hidden="true"
                style={{
                  width: 60,
                  height: 40,
                  borderRadius: 6,
                  background: selected ? "var(--c-card)" : "var(--c-line-2)",
                  color: selected ? "var(--c-blue-700)" : "var(--c-ink-3)",
                  display: "grid",
                  placeItems: "center",
                  border: selected ? "1px solid var(--c-blue-200)" : "1px solid var(--c-line)",
                }}
              >
                <VisibilityPreview value={opt.value} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span
                  style={{
                    fontWeight: 600,
                    fontSize: 15,
                    color: selected ? "var(--c-blue-700)" : "var(--c-ink)",
                  }}
                >
                  {opt.label}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    color: "var(--c-ink-3)",
                    lineHeight: 1.45,
                  }}
                >
                  {opt.short}
                </span>
                {selected && opt.value === "buildings" && (
                  <span
                    style={{
                      fontSize: 12,
                      color: "var(--c-warn)",
                      fontWeight: 600,
                      marginTop: 2,
                      display: "inline-flex",
                      gap: 5,
                      lineHeight: 1.4,
                    }}
                  >
                    <span aria-hidden="true">⚠</span>
                    The marker pattern can still hint at where damage occurred. Confirm this is OK.
                  </span>
                )}
              </div>
              <InfoHint align="end">{opt.description}</InfoHint>
            </label>
            {showKAnonymity && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 5,
                  padding: "8px 12px 10px 34px",
                  border: "1px solid var(--c-blue-700)",
                  borderTop: "1px dashed var(--c-blue-300)",
                  background: "var(--c-blue-50)",
                  borderRadius: "0 0 8px 8px",
                  marginTop: -8,
                }}
              >
                <label
                  htmlFor="k-anonymity-floor"
                  className="label-eyebrow"
                  style={{
                    marginBottom: 0,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    fontSize: 10,
                  }}
                >
                  Hide cells with fewer than
                  <InfoHint>
                    The public heatmap is built from H3 <b>hex cells</b>. Think of them as honeycomb
                    tiles, each roughly 200–500 m across. To prevent a single household from being
                    identifiable just because their hex is the only one with a report, any cell with
                    fewer than this many reports is dropped entirely from the public view.
                  </InfoHint>
                </label>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    id="k-anonymity-floor"
                    type="number"
                    min={1}
                    value={kAnonymity}
                    onChange={(e) => {
                      const next = Number.parseInt(e.target.value, 10);
                      if (!Number.isNaN(next) && next >= 1) onKAnonymityChange(next);
                    }}
                    style={{ width: 80 }}
                  />
                  <span style={{ fontSize: 11.5, color: "var(--c-ink-2)" }}>
                    reports per hex: fewer than this is hidden from the public.
                  </span>
                </div>
                {showLowKWarning && (
                  <div
                    style={{
                      fontSize: 11.5,
                      color: "var(--c-warn)",
                      fontWeight: 600,
                      marginTop: 2,
                      display: "flex",
                      gap: 5,
                      lineHeight: 1.4,
                    }}
                  >
                    <span aria-hidden="true">⚠</span>
                    <span>
                      In sparse areas a low floor can leave a single household identifiable.{" "}
                      <b>k = 5</b> is the common safe default.
                    </span>
                  </div>
                )}
              </div>
            )}
            {showInfraTypes && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 5,
                  padding: "8px 12px 10px 34px",
                  border: "1px solid var(--c-blue-700)",
                  borderTop: "1px dashed var(--c-blue-300)",
                  background: "var(--c-blue-50)",
                  borderRadius: "0 0 8px 8px",
                  marginTop: -8,
                }}
              >
                <span
                  className="label-eyebrow"
                  style={{
                    marginBottom: 0,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    fontSize: 10,
                  }}
                >
                  Which building categories can the public see?
                  <InfoHint>
                    Each report is tagged with the kind of place it's about (home, shop, school,
                    etc.). Untick any category you'd rather hide from the public. For example,
                    hiding <b>Government</b> can avoid drawing attention to damaged state
                    infrastructure during an active crisis.
                  </InfoHint>
                </span>
                <PublicInfraTypeEditor value={infraTypes} onChange={onInfraTypesChange} />
              </div>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

// Public infra-type filter. The wire is a whitelist (null = show all), but the
// form is a blacklist of types to hide:
//
//   wire whitelist = KNOWN_INFRA_TYPE_VALUES \ blacklist
//   wire null      = empty blacklist
//
// A saved whitelist enumerates the types known at save time, so a type added
// later is hidden for any crisis with a non-empty blacklist. Intentional.
//
// Mirrors the `InfraType` union in src/types/index.ts. Unknown saved values
// (set via the API) round-trip through the editor untouched.
const INFRA_TYPE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "residential", label: "Residential" },
  { value: "commercial", label: "Commercial" },
  { value: "government", label: "Government" },
  { value: "utility", label: "Utility" },
  { value: "transport", label: "Transport" },
  { value: "community", label: "Community" },
  { value: "public_spaces", label: "Public spaces" },
  { value: "other", label: "Other" },
];

const KNOWN_INFRA_TYPE_VALUES: readonly string[] = INFRA_TYPE_OPTIONS.map((o) => o.value);

function infraFilterApplies(visibility: PublicVisibility): boolean {
  return visibility === "buildings" || visibility === "full";
}

function infraTypeLabel(value: string): string {
  return INFRA_TYPE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

// Order-insensitive equality (the wire is a set); `undefined` counts as `null`.
function sameInfraFilter(a: string[] | null | undefined, b: string[] | null | undefined): boolean {
  const na = a ?? null;
  const nb = b ?? null;
  if (na === null && nb === null) return true;
  if (na === null || nb === null) return false;
  if (na.length !== nb.length) return false;
  const sa = [...na].sort();
  const sb = [...nb].sort();
  return sa.every((v, i) => v === sb[i]);
}

// Hidden set from a saved whitelist; null/undefined = nothing hidden.
function blacklistFromWhitelist(whitelist: string[] | null | undefined): string[] {
  if (whitelist == null) return [];
  const allowed = new Set(whitelist);
  return KNOWN_INFRA_TYPE_VALUES.filter((v) => !allowed.has(v));
}

// Wire whitelist from a `hidden` set, keeping unmanaged saved values. Returns
// `null` (show all) when nothing is hidden and there are no extras.
function whitelistFromBlacklist(hidden: string[], extras: string[]): string[] | null {
  if (hidden.length === 0 && extras.length === 0) return null;
  const allowed = KNOWN_INFRA_TYPE_VALUES.filter((v) => !hidden.includes(v));
  return [...allowed, ...extras];
}

function PublicInfraTypeEditor({
  value,
  onChange,
}: {
  value: string[] | null;
  onChange: (v: string[] | null) => void;
}) {
  // Blacklist in the UI, whitelist on the wire; extras round-trip through onChange.
  const extras = value === null ? [] : value.filter((v) => !KNOWN_INFRA_TYPE_VALUES.includes(v));
  const hidden = blacklistFromWhitelist(value);
  const hiddenAll = hidden.length === KNOWN_INFRA_TYPE_VALUES.length;

  const toggle = (slug: string) => {
    const currentlyHidden = hidden.includes(slug);
    const nextHidden = currentlyHidden ? hidden.filter((v) => v !== slug) : [...hidden, slug];
    onChange(whitelistFromBlacklist(nextHidden, extras));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 13, color: "var(--c-ink-3)" }}>
        Untick infra types to hide reports of that type from the public. Ticked types are visible.
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 6,
          padding: "10px 12px",
          border: "1px solid var(--c-line)",
          borderRadius: 8,
          background: "var(--c-card)",
        }}
      >
        {INFRA_TYPE_OPTIONS.map((opt) => {
          const isHidden = hidden.includes(opt.value);
          return (
            <label
              key={opt.value}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 14,
                cursor: "pointer",
              }}
            >
              <input type="checkbox" checked={!isHidden} onChange={() => toggle(opt.value)} />
              <span style={{ textDecoration: isHidden ? "line-through" : undefined }}>
                {opt.label}
              </span>
            </label>
          );
        })}
      </div>
      {hiddenAll && (
        <div
          style={{
            fontSize: 13,
            color: "var(--c-warn)",
            fontWeight: 600,
          }}
        >
          Every infra type is hidden. Set Public visibility to "None" instead, or re-tick at least
          one type.
        </div>
      )}
      {extras.length > 0 && (
        <div style={{ fontSize: 13, color: "var(--c-ink-3)" }}>
          Custom infra types preserved on save: {extras.join(", ")}.
        </div>
      )}
    </div>
  );
}

function TypeDisplay({ value }: { value: string }) {
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "10px 14px",
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 8,
        fontSize: 16,
        fontWeight: 600,
      }}
    >
      {value ? (
        <span style={{ display: "inline-flex", color: "var(--c-ink-3)" }}>
          <CrisisTypeGlyph type={value} size={22} />
        </span>
      ) : (
        <span aria-hidden="true" style={{ color: "var(--c-ink-3)" }}>
          •
        </span>
      )}
      <span>{value || "—"}</span>
    </div>
  );
}

function CountriesDisplay({
  codes,
  byIso2,
}: {
  codes: string[];
  byIso2: Map<string, CountryRow>;
}) {
  if (codes.length === 0)
    return <span style={{ fontSize: 15, color: "var(--c-ink-3)" }}>No countries</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {codes.map((code) => {
        const row = byIso2.get(code);
        return (
          <span
            key={code}
            className="chip"
            style={{ padding: "5px 12px", fontSize: 14, fontWeight: 600 }}
          >
            {row ? row.name : code}
          </span>
        );
      })}
    </div>
  );
}

function AreaDisplay({
  hasGeometry,
  geometry,
  countries,
  pmtilesUrl,
}: {
  hasGeometry: boolean;
  geometry: GeoJSONPolygon | GeoJSONMultiPolygon | null;
  countries: string[];
  pmtilesUrl?: string | null;
}) {
  if (!hasGeometry) {
    return (
      <div
        style={{
          padding: "14px 16px",
          background: "var(--c-card)",
          border: "1px dashed var(--c-line)",
          borderRadius: 8,
          fontSize: 15,
          color: "var(--c-ink-3)",
        }}
      >
        {countries.length > 0
          ? `No polygon stored. Resolves at runtime to the union of ${countries.join(", ")}.`
          : "No area set."}
      </div>
    );
  }
  if (!geometry) {
    return (
      <div
        style={{
          height: 240,
          borderRadius: 8,
          border: "1px solid var(--c-line)",
          display: "grid",
          placeItems: "center",
          fontSize: 15,
          color: "var(--c-ink-3)",
        }}
      >
        Loading saved area…
      </div>
    );
  }
  return <SavedAreaMap geometry={geometry} height={340} pmtilesUrl={pmtilesUrl} />;
}

// Steps that can never be removed (lock cue). Mirrors `isLockedKind` in formBuilder/useFormBuilderState.
const LOCKED_FORM_KINDS = new Set(["photo_and_damage", "location"]);

// "Community form" step strip: citizen steps as numbered tiles, locked ones
// flagged, disabled ones ghosted so the coordinator sees what they could enable.
function FormStepStrip({ schema }: { schema: FormSchema }) {
  const enabled = schema.pages.filter((p) => p.enabled);
  const off = schema.pages.filter((p) => !p.enabled);

  const tileBase: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    borderRadius: 8,
    padding: "7px 11px",
    fontSize: 13,
    lineHeight: 1.2,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <span className="chip blue" style={{ alignSelf: "flex-start" }}>
        {enabled.length} of {schema.pages.length} steps shown
      </span>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {enabled.map((page, idx) => (
          <span
            key={`${page.kind}:${idx}`}
            style={{
              ...tileBase,
              background: "var(--crises-well)",
              border: "1px solid var(--crises-edge)",
              fontWeight: 600,
              color: "var(--c-ink)",
            }}
          >
            <span
              aria-hidden="true"
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 18,
                height: 18,
                borderRadius: 999,
                background: "var(--c-blue-700)",
                color: "#fff",
                fontSize: 10.5,
                fontWeight: 700,
                flexShrink: 0,
              }}
            >
              {idx + 1}
            </span>
            {formPageLabel(page)}
            {LOCKED_FORM_KINDS.has(page.kind) && (
              <span aria-label="Always on" title="Always on" style={{ color: "var(--c-ink-4)" }}>
                <Icon.lock width="12" height="12" />
              </span>
            )}
          </span>
        ))}
      </div>

      {off.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          {off.map((page, idx) => (
            <span
              key={`${page.kind}:${idx}`}
              style={{
                ...tileBase,
                background: "transparent",
                border: "1px dashed var(--crises-edge-strong)",
                fontWeight: 500,
                color: "var(--c-ink-3)",
              }}
            >
              {formPageLabel(page)}
            </span>
          ))}
          <span style={{ fontSize: 12.5, color: "var(--c-ink-3)" }}>
            Off now. Open the editor to enable.
          </span>
        </div>
      )}
    </div>
  );
}

function CrisisDetailPanel({
  crisis,
  jobs,
  ingestPending,
  ingestError,
  statusPending,
  confirmActivate,
  onIngest,
  onStatus,
  onAskActivate,
  onCancelActivate,
  onPatched,
}: {
  crisis: AdminCrisis;
  jobs: CrisisJob[];
  ingestPending: boolean;
  ingestError: string | null;
  statusPending: boolean;
  confirmActivate: boolean;
  onIngest: () => void;
  onStatus: (id: string, status: CrisisStatus) => void;
  onAskActivate: () => void;
  onCancelActivate: () => void;
  onPatched: (c: AdminCrisis) => void;
}) {
  const tz = useAdminTimezone();
  const [editName, setEditName] = useState(crisis.name);
  const [editType, setEditType] = useState(crisis.type);
  const [editCountries, setEditCountries] = useState<string[]>(crisis.countries);
  const [editArea, setEditArea] = useState<CrisisAreaValue>({ mode: "empty" });
  const [editActivateAt, setEditActivateAt] = useState<string | null>(crisis.activate_at ?? null);
  const [editActivateOnIngest, setEditActivateOnIngest] = useState<boolean>(
    crisis.activate_on_ingest_success ?? false,
  );
  // Switching to `full` requires ticking the confirm checkbox before Save;
  // `confirmFullChecked` tracks it. The API does not enforce this.
  const [editVisibility, setEditVisibility] = useState<PublicVisibility>(crisis.public_visibility);
  const [editKAnonymity, setEditKAnonymity] = useState<number>(crisis.heatmap_k_anonymity);
  // Public infra-type whitelist: `null` = show all; empty arrays are normalised
  // to `null` server-side.
  const [editInfraTypes, setEditInfraTypes] = useState<string[] | null>(
    crisis.public_infra_types ?? null,
  );
  const [editingVisibility, setEditingVisibility] = useState(false);
  const [confirmFullVisibility, setConfirmFullVisibility] = useState(false);
  const [confirmFullChecked, setConfirmFullChecked] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [editingType, setEditingType] = useState(false);
  const [editingCountries, setEditingCountries] = useState(false);
  const [editingArea, setEditingArea] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [confirmAreaChange, setConfirmAreaChange] = useState(false);
  const [confirmIngest, setConfirmIngest] = useState(false);
  // Pre-flight ingest estimate (count + per-phase ETA). Auto-fetched when the
  // crisis has an area (a cheap precomputed-grid lookup); cleared when the
  // crisis or its geometry changes so a stale count never shows.
  const [estimate, setEstimate] = useState<IngestEstimate | null>(null);
  const [estimatePending, setEstimatePending] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [savedGeometry, setSavedGeometry] = useState<GeoJSONPolygon | GeoJSONMultiPolygon | null>(
    null,
  );
  const [countryRows, setCountryRows] = useState<CountryRow[]>([]);

  const setupRef = useRef<HTMLDivElement>(null);
  const ingestRef = useRef<HTMLDivElement>(null);
  const lifecycleRef = useRef<HTMLDivElement>(null);

  // Published citizen form for the step preview; `null` while loading or on error.
  const [communityForm, setCommunityForm] = useState<FormSchema | null>(null);
  useEffect(() => {
    let cancelled = false;
    setCommunityForm(null);
    void getForm(crisis.id)
      .then((res) => {
        if (!cancelled) setCommunityForm(res.schema);
      })
      .catch(() => {
        // Leave null; the section still offers the editor button.
      });
    return () => {
      cancelled = true;
    };
  }, [crisis.id]);

  const closeEditors = useCallback(() => {
    setEditingName(false);
    setEditingType(false);
    setEditingCountries(false);
    setEditingArea(false);
    setEditingVisibility(false);
  }, []);

  useEffect(() => {
    setEditName(crisis.name);
    setEditType(crisis.type);
    setEditCountries(crisis.countries);
    setEditArea({ mode: "empty" });
    setEditActivateAt(crisis.activate_at ?? null);
    setEditActivateOnIngest(crisis.activate_on_ingest_success ?? false);
    setEditVisibility(crisis.public_visibility);
    setEditKAnonymity(crisis.heatmap_k_anonymity);
    setEditInfraTypes(crisis.public_infra_types ?? null);
    setConfirmFullVisibility(false);
    setConfirmFullChecked(false);
    setMetaError(null);
    setConfirmAreaChange(false);
    setConfirmIngest(false);
    setEstimate(null);
    setEstimateError(null);
    closeEditors();
  }, [
    crisis.name,
    crisis.type,
    crisis.countries,
    crisis.activate_at,
    crisis.activate_on_ingest_success,
    crisis.public_visibility,
    crisis.heatmap_k_anonymity,
    crisis.public_infra_types,
    closeEditors,
  ]);

  // Saved polygon for the area row's preview; skipped when the crisis has no geometry.
  useEffect(() => {
    if (!crisis.has_geometry) {
      setSavedGeometry(null);
      return;
    }
    const ctrl = new AbortController();
    fetchCrisisGeometry(crisis.id, { signal: ctrl.signal })
      .then((geom) => setSavedGeometry(geom))
      .catch((err: unknown) => {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        console.warn("Failed to load crisis geometry preview:", err);
      });
    return () => ctrl.abort();
  }, [crisis.id, crisis.has_geometry]);

  // Country names for the chips (cached by `fetchCountries`); falls back to the ISO-2 code.
  useEffect(() => {
    let cancelled = false;
    fetchCountries()
      .then((rows) => {
        if (!cancelled) setCountryRows(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  const countryByIso2 = useMemo(() => {
    const m = new Map<string, CountryRow>();
    for (const c of countryRows) m.set(c.iso2, c);
    return m;
  }, [countryRows]);

  const countriesChanged =
    editCountries.length !== crisis.countries.length ||
    editCountries.some((c, i) => c !== crisis.countries[i]);
  // An opened Search tab with no places picked is still "no change": not dirty.
  const areaChanged =
    editArea.mode !== "empty" && !(editArea.mode === "search" && editArea.selections.length === 0);
  const metaChanged = editName !== crisis.name || editType !== crisis.type;
  const scheduleChanged =
    editActivateAt !== (crisis.activate_at ?? null) ||
    editActivateOnIngest !== (crisis.activate_on_ingest_success ?? false);
  const visibilityChanged = editVisibility !== crisis.public_visibility;
  const kAnonymityChanged = editKAnonymity !== crisis.heatmap_k_anonymity;
  const infraTypesChanged = !sameInfraFilter(editInfraTypes, crisis.public_infra_types);
  // Block saves that hide every known infra type; use visibility "None" instead.
  // A `null` filter (show all) is always valid.
  const infraTypesAllHidden =
    infraFilterApplies(editVisibility) &&
    editInfraTypes !== null &&
    KNOWN_INFRA_TYPE_VALUES.every((v) => !editInfraTypes.includes(v));
  const dirty =
    metaChanged ||
    countriesChanged ||
    areaChanged ||
    scheduleChanged ||
    visibilityChanged ||
    kAnonymityChanged ||
    infraTypesChanged;

  const performSave = async () => {
    setMetaError(null);
    setSavingMeta(true);
    try {
      const payload: AdminCrisisPatch = {};
      if (editName !== crisis.name) payload.name = editName;
      if (editType !== crisis.type) payload.type = editType;
      if (countriesChanged) payload.countries = editCountries;
      if (areaChanged) {
        const inline = geometryFromArea(editArea);
        if (inline) {
          payload.geometry = inline;
        } else if (editArea.mode === "countries-only") {
          // Countries-only emits no polygon, so resolve the union here and send it.
          // PATCH can't resolve it server-side without clobbering custom-drawn
          // polygons when only the country list (metadata) is edited.
          if (editCountries.length === 0) {
            throw new Error("Pick at least one country before saving countries-only.");
          }
          const union = await fetchCountriesGeometry(editCountries);
          if (!union) {
            throw new Error("Could not resolve the polygon for the selected countries.");
          }
          payload.geometry = { polygon: union };
        }
      }
      if (scheduleChanged) {
        payload.activate_at = editActivateAt;
        payload.activate_on_ingest_success = editActivateOnIngest;
      }
      if (visibilityChanged) payload.public_visibility = editVisibility;
      if (kAnonymityChanged) payload.heatmap_k_anonymity = editKAnonymity;
      if (infraTypesChanged) {
        // Send `null` explicitly for "Show all"; normalise empty arrays to `null` here too.
        payload.public_infra_types =
          editInfraTypes !== null && editInfraTypes.length > 0 ? editInfraTypes : null;
      }
      const updated = await patchAdminCrisis(crisis.id, payload);
      onPatched(updated);
      setEditArea({ mode: "empty" });
      setConfirmAreaChange(false);
      setConfirmFullVisibility(false);
      setConfirmFullChecked(false);
      closeEditors();
    } catch (err) {
      setMetaError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingMeta(false);
    }
  };

  const saveMeta = async () => {
    if (areaChanged) {
      setConfirmAreaChange(true);
      return;
    }
    // Switching to `full` requires the modal-confirm checkbox before Save.
    if (visibilityChanged && editVisibility === "full" && !confirmFullChecked) {
      setConfirmFullVisibility(true);
      return;
    }
    await performSave();
  };

  const ingestJob = jobs.find((j) => j.job_type === "ingest_buildings");
  const ingestSucceeded = ingestJob?.status === "succeeded";
  // A countries-only crisis has `geometry=NULL` but is still ingestable (the
  // worker resolves the polygon from `countries`), so gate on either source.
  const hasIngestSource = crisis.has_geometry || crisis.countries.length > 0;

  const runEstimate = useCallback(async () => {
    setEstimateError(null);
    setEstimatePending(true);
    try {
      setEstimate(await estimateIngestBuildings(crisis.id));
    } catch (err) {
      setEstimate(null);
      setEstimateError(err instanceof Error ? err.message : String(err));
    } finally {
      setEstimatePending(false);
    }
  }, [crisis.id]);

  // Gated on `has_geometry`: the endpoint 422s without a polygon (e.g. a
  // countries-only crisis with no resolved geometry).
  useEffect(() => {
    if (crisis.has_geometry) {
      void runEstimate();
    }
  }, [crisis.has_geometry, runEstimate]);

  const setupState: StepState = { kind: "done", sub: "saved" };
  // Status-strip colours. Buildings: green once loaded, yellow if not;
  // running/failed keep blue/red so a failed load never reads as pending.
  // Activation: green when live, grey when archived, yellow while inactive.
  const ingestState: StepState =
    ingestJob?.status === "running"
      ? { kind: "running", sub: "loading…" }
      : ingestJob?.status === "failed"
        ? { kind: "failed", sub: "load failed" }
        : ingestSucceeded
          ? {
              kind: "done",
              sub: `${(crisis.buildings_ingested_count ?? 0).toLocaleString()} buildings`,
            }
          : { kind: "warn", sub: "not loaded" };
  const lifecycleState: StepState =
    crisis.status === "active"
      ? { kind: "done", sub: "● live" }
      : crisis.status === "archived"
        ? { kind: "muted", sub: "archived" }
        : crisis.activate_at
          ? {
              kind: "warn",
              sub: `scheduled for ${formatDisplay(crisis.activate_at, tz)}`,
            }
          : crisis.activate_on_ingest_success
            ? { kind: "warn", sub: "go live on ingest success" }
            : { kind: "warn", sub: "inactive" };

  const scrollToStep = (index: 1 | 2 | 3) => {
    // Step 2 is Activation, step 3 Building shapes.
    const ref = index === 1 ? setupRef : index === 2 ? lifecycleRef : ingestRef;
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <>
      <div className="crumb-strip">
        <span style={{ color: "var(--c-ink-3)" }}>Editing</span>
        <span style={{ color: "var(--c-ink-4)" }}>›</span>
        <span className="crumb-active" title={crisis.name}>
          {crisis.name}
        </span>
        <span
          className={statusChipClass(crisis.status)}
          style={{ padding: "2px 8px", fontSize: 10.5, fontWeight: 600, flexShrink: 0 }}
        >
          {statusLabel(crisis.status)}
        </span>
        <span className="crumb-spacer" />
        <CrumbTzChip />
      </div>

      <CrisisStepper
        setupState={setupState}
        ingestState={ingestState}
        lifecycleState={lifecycleState}
        onStepClick={scrollToStep}
      />

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          // Always reserve the scrollbar gutter so the centered crisis-grid
          // doesn't jump left when an expanding section makes content scroll.
          scrollbarGutter: "stable",
          padding: "18px 22px 28px",
          background: "var(--c-bg)",
        }}
      >
        <div className="crisis-grid">
          <SectionBlock
            number="1"
            title="Configuration"
            description="Everything below was set up when the crisis was created. Edit any field, then save at the bottom of this section."
            data-span={12}
            dataTour="setup-config"
            hint={
              <>
                Each field has its own <b>Edit</b> control. Changing the area or going to "Full
                report detail" triggers an extra confirmation because both are hard to undo.
              </>
            }
            innerRef={setupRef}
          >
            <FieldRow
              label="Name"
              editing={editingName}
              onEdit={() => setEditingName(true)}
              display={
                <span style={{ fontSize: 15, fontWeight: 600, color: "var(--c-ink)" }}>
                  {crisis.name}
                </span>
              }
              editor={
                <input
                  id="edit-name"
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              }
            />

            <FieldRow
              label="Crisis type"
              editing={editingType}
              onEdit={() => setEditingType(true)}
              display={<TypeDisplay value={crisis.type} />}
              editor={<TypeGrid value={editType} onChange={setEditType} />}
            />

            <FieldRow
              label="Countries"
              editing={editingCountries}
              onEdit={() => setEditingCountries(true)}
              display={<CountriesDisplay codes={crisis.countries} byIso2={countryByIso2} />}
              editor={<CountriesMultiselect value={editCountries} onChange={setEditCountries} />}
            />

            <FieldRow
              label="Crisis area"
              wide
              editing={editingArea}
              onEdit={() => setEditingArea(true)}
              display={
                <AreaDisplay
                  hasGeometry={crisis.has_geometry}
                  geometry={savedGeometry}
                  countries={crisis.countries}
                  pmtilesUrl={crisis.pmtiles_url ?? null}
                />
              }
              editor={
                <>
                  <div
                    style={{
                      fontSize: 14,
                      color: "var(--c-ink-3)",
                      padding: "10px 12px",
                      background: "var(--c-blue-50)",
                      border: "1px solid var(--c-blue-200)",
                      borderRadius: 6,
                      marginBottom: 6,
                    }}
                  >
                    Replacing the area invalidates building ingest, so you'll need to re-run it.
                  </div>
                  <CrisisAreaPicker
                    value={editArea}
                    onChange={setEditArea}
                    countries={editCountries}
                  />
                </>
              }
            />

            {confirmAreaChange && (
              <div
                className="card"
                style={{
                  borderColor: "var(--c-warn)",
                  background: "var(--c-warn-bg)",
                  marginBottom: 10,
                }}
              >
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 700,
                    color: "var(--c-warn)",
                    marginBottom: 6,
                  }}
                >
                  Change crisis area?
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 10 }}>
                  Changing the area will invalidate building ingest, so you'll need to run it again
                  before footprints reappear on the citizen map.
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    className="btn secondary"
                    style={{ flex: 1 }}
                    onClick={() => setConfirmAreaChange(false)}
                    disabled={savingMeta}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn"
                    style={{ flex: 2 }}
                    onClick={() => void performSave()}
                    disabled={savingMeta}
                  >
                    {savingMeta ? "Saving…" : "Change area"}
                  </button>
                </div>
              </div>
            )}

            <FieldRow
              label="Public visibility"
              wide
              editing={editingVisibility}
              onEdit={() => setEditingVisibility(true)}
              display={
                <PublicVisibilityDisplay
                  value={crisis.public_visibility}
                  kAnonymity={crisis.heatmap_k_anonymity}
                  infraTypes={crisis.public_infra_types ?? null}
                />
              }
              editor={
                <PublicVisibilityEditor
                  value={editVisibility}
                  onChange={(v) => {
                    setEditVisibility(v);
                    // Reset the confirm tick on every change so a stale ack can't carry through.
                    setConfirmFullVisibility(false);
                    setConfirmFullChecked(false);
                  }}
                  kAnonymity={editKAnonymity}
                  onKAnonymityChange={setEditKAnonymity}
                  infraTypes={editInfraTypes}
                  onInfraTypesChange={setEditInfraTypes}
                />
              }
            />

            {confirmFullVisibility && (
              <div
                className="card"
                style={{
                  borderColor: "var(--c-warn)",
                  background: "var(--c-warn-bg)",
                  marginBottom: 10,
                }}
              >
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 700,
                    color: "var(--c-warn)",
                    marginBottom: 6,
                  }}
                >
                  Make every report public?
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 10 }}>
                  Photos, descriptions, and locations for every report (
                  <b>including ones already submitted</b>) will be visible to anyone. This is hard
                  to reverse.
                </div>
                <label
                  htmlFor="confirm-full-visibility"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontSize: 14,
                    marginBottom: 10,
                    cursor: "pointer",
                  }}
                >
                  <input
                    id="confirm-full-visibility"
                    type="checkbox"
                    checked={confirmFullChecked}
                    onChange={(e) => setConfirmFullChecked(e.target.checked)}
                  />
                  <span>I understand all historical reports will become public.</span>
                </label>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    className="btn secondary"
                    style={{ flex: 1 }}
                    onClick={() => {
                      setConfirmFullVisibility(false);
                      setConfirmFullChecked(false);
                    }}
                    disabled={savingMeta}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn warn"
                    style={{ flex: 2 }}
                    onClick={() => void performSave()}
                    disabled={!confirmFullChecked || savingMeta}
                  >
                    {savingMeta ? "Saving…" : "Publish everything"}
                  </button>
                </div>
              </div>
            )}

            {metaError && (
              <div
                style={{
                  fontSize: 14,
                  color: "var(--c-danger)",
                  marginBottom: 8,
                }}
              >
                {metaError}
              </div>
            )}
            {(dirty ||
              editingName ||
              editingType ||
              editingCountries ||
              editingArea ||
              editingVisibility) && (
              <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
                <button
                  type="button"
                  className="btn secondary"
                  style={{ flex: 1 }}
                  onClick={() => {
                    setEditName(crisis.name);
                    setEditType(crisis.type);
                    setEditCountries(crisis.countries);
                    setEditArea({ mode: "empty" });
                    setEditVisibility(crisis.public_visibility);
                    setEditKAnonymity(crisis.heatmap_k_anonymity);
                    setEditInfraTypes(crisis.public_infra_types ?? null);
                    setConfirmFullVisibility(false);
                    setConfirmFullChecked(false);
                    setMetaError(null);
                    setConfirmAreaChange(false);
                    closeEditors();
                  }}
                  disabled={savingMeta}
                >
                  Discard
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ flex: 2 }}
                  onClick={saveMeta}
                  disabled={!dirty || savingMeta || infraTypesAllHidden}
                  title={
                    infraTypesAllHidden
                      ? "Re-tick at least one infra type, or set Public visibility to 'None'."
                      : undefined
                  }
                >
                  {savingMeta ? "Saving…" : dirty ? "Save changes" : "No changes yet"}
                </button>
              </div>
            )}

            <dl className="meta-grid" style={{ marginBottom: 0 }}>
              <dt>Started</dt>
              <dd title={crisis.started_at ?? undefined}>
                {crisis.started_at ? formatDisplay(crisis.started_at, tz) : "—"}
              </dd>
              <dt>Ended</dt>
              <dd title={crisis.ended_at ?? undefined}>
                {crisis.ended_at ? formatDisplay(crisis.ended_at, tz) : "—"}
              </dd>
              <dt>Buildings loaded</dt>
              <dd
                title={
                  crisis.buildings_ingested_at
                    ? `Last loaded ${formatDisplay(crisis.buildings_ingested_at, tz)}`
                    : undefined
                }
              >
                {crisis.buildings_ingested_count != null
                  ? `${crisis.buildings_ingested_count.toLocaleString()} buildings`
                  : "—"}
              </dd>
            </dl>
          </SectionBlock>

          <SectionBlock
            number="2"
            accent={crisis.status === "active" ? "default" : "warn"}
            title="Activation"
            description="Whether citizens can see this crisis and submit reports for it."
            hint={
              <>
                You can activate manually with the button below, schedule a specific moment, or
                auto-activate when building data finishes loading. Deactivating later hides the
                crisis again but keeps every report intact.
              </>
            }
            innerRef={lifecycleRef}
            data-span={6}
            dataTour="setup-lifecycle"
          >
            <div className="label-eyebrow" style={{ marginBottom: 6 }}>
              How should it go live?
            </div>
            <ScheduleActivationField
              value={{
                activate_at: editActivateAt,
                activate_on_ingest_success: editActivateOnIngest,
              }}
              onChange={(v) => {
                setEditActivateAt(v.activate_at);
                setEditActivateOnIngest(v.activate_on_ingest_success);
              }}
              disabled={crisis.status !== "inactive"}
              ingestInScope={jobs.some((j) => j.job_type === "ingest_buildings") || hasIngestSource}
              ingestInScopeReason={
                jobs.some((j) => j.job_type === "ingest_buildings")
                  ? undefined
                  : hasIngestSource
                    ? "Load building data first (step 3); this option only works when a building load is in progress or queued."
                    : "This crisis has no area or country set. Building load can't run, so this option can't fire."
              }
            />

            <div
              className="label-eyebrow"
              style={{
                marginTop: 16,
                marginBottom: 8,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              Change state
              <InfoHint>
                These buttons change the crisis's lifecycle directly. Use <b>Activate</b> when
                you're ready to go live, <b>Deactivate</b> to temporarily hide it from citizens, or{" "}
                <b>Archive</b> when the event is over.
              </InfoHint>
            </div>

            {confirmActivate ? (
              <div
                className="card"
                style={{
                  borderColor: "var(--c-warn)",
                  background: "var(--c-warn-bg)",
                  marginBottom: 4,
                }}
              >
                <div
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    color: "var(--c-warn)",
                    marginBottom: 6,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Icon.alert width="16" height="16" />
                  Publish this crisis to citizens?
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.55, marginBottom: 10 }}>
                  The moment you confirm, every citizen who opens the app sees this crisis in their
                  picker and can submit damage reports against it.
                </div>
                {!ingestSucceeded && (
                  <div
                    style={{
                      borderRadius: 8,
                      border: "1px solid var(--c-warn)",
                      background: "var(--c-card)",
                      padding: "10px 12px",
                      marginBottom: 10,
                      display: "flex",
                      gap: 8,
                    }}
                  >
                    <Icon.alert
                      width="16"
                      height="16"
                      style={{ flexShrink: 0, marginTop: 2, color: "var(--c-warn)" }}
                    />
                    <div>
                      <div
                        style={{
                          fontSize: 14,
                          fontWeight: 700,
                          color: "var(--c-warn)",
                          marginBottom: 2,
                        }}
                      >
                        Building data not loaded yet
                      </div>
                      <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--c-ink-2)" }}>
                        Citizens will see this crisis in the picker but won't be able to tap a
                        specific building until building data finishes loading. You can still
                        proceed if you want to start collecting form-only reports.
                      </div>
                    </div>
                  </div>
                )}
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    className="btn secondary"
                    style={{ flex: 1 }}
                    onClick={onCancelActivate}
                    disabled={statusPending}
                  >
                    Not yet
                  </button>
                  <button
                    type="button"
                    className="btn"
                    style={{ flex: 2 }}
                    onClick={() => onStatus(crisis.id, "active")}
                    disabled={statusPending}
                  >
                    {statusPending ? "Going live…" : "Yes, go live"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="lifecycle-bar" data-tour="lifecycle">
                {crisis.status !== "active" && (
                  <button
                    type="button"
                    className="lifecycle-btn is-primary"
                    style={!ingestSucceeded ? { opacity: 0.75 } : undefined}
                    title={
                      !ingestSucceeded
                        ? "No building data loaded yet. Citizens won't be able to tap a specific building when reporting."
                        : "Citizens will immediately see this crisis and can submit reports for it."
                    }
                    onClick={onAskActivate}
                    disabled={statusPending}
                  >
                    <span className="ltag">GO LIVE</span>
                    Activate
                  </button>
                )}
                {crisis.status === "active" && (
                  <button
                    type="button"
                    className="lifecycle-btn is-warn"
                    onClick={() => onStatus(crisis.id, "inactive")}
                    disabled={statusPending}
                    title="Hide the crisis from citizens. Existing reports stay intact and reappear when reactivated."
                  >
                    <span className="ltag">HIDE</span>
                    Deactivate
                  </button>
                )}
                {crisis.status !== "archived" && (
                  <button
                    type="button"
                    className="lifecycle-btn is-danger"
                    onClick={() => onStatus(crisis.id, "archived")}
                    disabled={statusPending}
                    title="Close the crisis for good. It stays in the historical record but cannot accept new reports."
                  >
                    <span className="ltag">CLOSE</span>
                    Archive
                  </button>
                )}
                {crisis.status === "archived" && (
                  <button
                    type="button"
                    className="lifecycle-btn"
                    onClick={() => onStatus(crisis.id, "inactive")}
                    disabled={statusPending}
                    title="Pull the crisis out of archive and back into the inactive state for review."
                  >
                    <span className="ltag">RESTORE</span>
                    Move to inactive
                  </button>
                )}
              </div>
            )}
          </SectionBlock>

          <SectionBlock
            number="3"
            title="Building shapes"
            description="Lets citizens tap a specific building when reporting damage. Downloaded from open map data."
            dataTour="setup-ingest"
            hint={
              <>
                Building outlines come from <b>Overture Maps</b>, an open building dataset. After
                this runs, citizens see every building inside the crisis area on their map and can
                tap one to file a report against it.
              </>
            }
            innerRef={ingestRef}
            data-span={6}
          >
            <div
              style={{
                display: "flex",
                gap: 10,
                padding: "12px 14px",
                background: "var(--c-danger-bg)",
                border: "1px solid var(--c-danger)",
                borderRadius: 10,
                fontSize: 14,
                lineHeight: 1.5,
                color: "var(--c-danger)",
                marginBottom: 12,
              }}
            >
              <Icon.alert width="18" height="18" style={{ flexShrink: 0, marginTop: 1 }} />
              <div>
                <b>Heavy job.</b> Downloads every building shape inside the crisis area: several
                minutes, real server load. Re-running replaces the previous load entirely.
              </div>
            </div>
            {/* Pre-flight estimate. Hidden while a run is in progress; the JobRow shows live progress. */}
            {hasIngestSource && ingestJob?.status !== "running" && !ingestPending && (
              <div style={{ marginBottom: 12 }}>
                {estimatePending && !estimate && (
                  <div style={{ fontSize: 14, color: "var(--c-ink-3)" }}>
                    Estimating size &amp; time…
                  </div>
                )}
                {estimateError && (
                  <div style={{ fontSize: 14, color: "var(--c-danger)" }}>
                    Couldn't estimate: {estimateError}
                  </div>
                )}
                {estimate && (
                  <div
                    className="card"
                    style={{
                      background: "var(--c-blue-50)",
                      border: "1px solid var(--c-blue-200)",
                      marginBottom: 0,
                    }}
                  >
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
                      ≈ {estimate.approx_building_count.toLocaleString()} buildings{" "}
                      <span style={{ fontWeight: 400, color: "var(--c-ink-3)" }}>
                        (upper bound)
                      </span>
                    </div>
                    <div style={{ fontSize: 14, lineHeight: 1.5 }}>
                      Estimated time <b>{formatDuration(estimate.est_seconds.total)}</b>:{" "}
                      {formatDuration(estimate.est_seconds.download)} downloading +{" "}
                      {formatDuration(estimate.est_seconds.load)} importing
                    </div>
                  </div>
                )}
              </div>
            )}
            {confirmIngest ? (
              <div
                className="card"
                style={{
                  borderColor: "var(--c-danger)",
                  background: "var(--c-danger-bg)",
                  marginBottom: 0,
                }}
              >
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 700,
                    color: "var(--c-danger)",
                    marginBottom: 6,
                  }}
                >
                  Start loading building data now?
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 10 }}>
                  This kicks off a long-running job against the Overture release for this crisis's
                  area. Make sure the area is final. Re-running replaces the previous load entirely.
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    className="btn secondary"
                    style={{ flex: 1 }}
                    onClick={() => setConfirmIngest(false)}
                    disabled={ingestPending}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn danger"
                    style={{ flex: 2 }}
                    onClick={() => {
                      setConfirmIngest(false);
                      onIngest();
                    }}
                    disabled={ingestPending}
                  >
                    {ingestPending
                      ? "Starting…"
                      : ingestJob
                        ? "Yes, re-load buildings"
                        : "Yes, load buildings"}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="btn danger"
                style={{ width: "100%" }}
                onClick={() => setConfirmIngest(true)}
                disabled={ingestPending || ingestJob?.status === "running" || !hasIngestSource}
              >
                {ingestPending || ingestJob?.status === "running"
                  ? "Loading…"
                  : ingestJob
                    ? "Re-load buildings"
                    : "Load building data"}
              </button>
            )}
            {!hasIngestSource && (
              <div style={{ fontSize: 14, color: "var(--c-ink-3)", marginTop: 8 }}>
                This crisis has no area or country set, so no building data can be loaded.
              </div>
            )}
            {ingestError && (
              <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>
                {ingestError}
              </div>
            )}
            {ingestJob && (
              <JobRow
                job={ingestJob}
                buildingsIngestedCount={
                  ingestJob.status === "succeeded" ? crisis.buildings_ingested_count : null
                }
              />
            )}
          </SectionBlock>

          <SectionBlock
            number="4"
            title="Community form"
            description="The steps citizens go through when reporting damage for this crisis."
            hint={
              <>
                Reorder steps, toggle optional ones on or off, and add your own questions in the
                full editor. <b>Photo & damage</b> and <b>Location</b> are always on.
              </>
            }
            data-span={12}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {communityForm ? (
                <FormStepStrip schema={communityForm} />
              ) : (
                <span style={{ fontSize: 13, color: "var(--c-ink-3)" }}>Loading current form…</span>
              )}
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  type="button"
                  className="btn secondary"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
                  onClick={() => {
                    window.location.hash = `#/crises/${crisis.id}/form`;
                  }}
                >
                  <Icon.edit width="13" height="13" />
                  Edit form
                </button>
              </div>
            </div>
          </SectionBlock>
        </div>
      </div>
    </>
  );
}

function CrisisStepper({
  setupState,
  ingestState,
  lifecycleState,
  onStepClick,
}: {
  setupState: StepState;
  ingestState: StepState;
  lifecycleState: StepState;
  onStepClick: (index: 1 | 2 | 3) => void;
}) {
  return (
    <div className="status-rail" aria-label="Crisis progress">
      <StatusCell
        index={1}
        title="Configure"
        hint="Crisis identity, area, time window, and what citizens can see"
        state={setupState}
        onClick={() => onStepClick(1)}
      />
      <StatusCell
        index={2}
        title="Make it live"
        hint="Publish to citizens so they can see and submit reports"
        state={lifecycleState}
        onClick={() => onStepClick(2)}
      />
      <StatusCell
        index={3}
        title="Load building data"
        hint="Pull building footprints inside the area from Overture Maps"
        state={ingestState}
        onClick={() => onStepClick(3)}
      />
    </div>
  );
}

function StatusCell({
  index,
  title,
  hint,
  state,
  onClick,
}: {
  index: 1 | 2 | 3;
  title: string;
  hint: string;
  state: StepState;
  onClick: () => void;
}) {
  const mod =
    state.kind === "done"
      ? " is-done"
      : state.kind === "active" || state.kind === "running"
        ? " is-active"
        : state.kind === "failed"
          ? " is-failed"
          : state.kind === "warn"
            ? " is-warn"
            : state.kind === "muted"
              ? " is-muted"
              : "";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`status-cell${mod}`}
      title={hint}
      aria-label={`Step ${index}: ${title}, ${state.sub}`}
    >
      <span className="step-num">Step {index}</span>
      <span className="step-title">
        <StatusGlyph kind={state.kind} index={index} />
        {title}
      </span>
      <span className="step-sub">{state.sub}</span>
    </button>
  );
}

function StatusGlyph({ index, kind }: { index: number; kind: StepKind }) {
  if (kind === "done") return <span className="step-glyph">✓</span>;
  if (kind === "running")
    return (
      <span className="step-glyph">
        <span className="spin" />
      </span>
    );
  if (kind === "failed") return <span className="step-glyph">!</span>;
  return <span className="step-glyph">{index}</span>;
}

function JobRow({
  job,
  buildingsIngestedCount,
}: {
  job: CrisisJob;
  // `crises.buildings_ingested_count`, shown after success; null hides it (no
  // authoritative count while running or after a failure).
  buildingsIngestedCount?: number | null;
}) {
  const tz = useAdminTimezone();
  const chip =
    job.status === "succeeded"
      ? "chip safe"
      : job.status === "failed"
        ? "chip danger"
        : "chip blue";
  const running = job.status === "running";
  const count = job.progress_count ?? null;
  const total = job.progress_total ?? null;
  const pct = running && count != null && total != null && total > 0 ? count / total : null;
  const etaSeconds = running ? liveEtaSeconds(job) : null;
  return (
    <div
      style={{
        marginTop: 12,
        padding: 10,
        borderRadius: 8,
        background: "var(--c-blue-50)",
        border: "1px solid var(--c-blue-200)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span className="mono" style={{ fontSize: 14, fontWeight: 600 }}>
          {jobTypeLabel(job.job_type)}
        </span>
        <span className={chip} style={{ padding: "4px 11px", fontSize: 14 }}>
          {job.status === "running" && <span className="spin" />}
          {job.status}
        </span>
      </div>
      <div className="mono" style={{ fontSize: 14, color: "var(--c-ink-3)" }}>
        started {formatDisplay(job.started_at, tz)}
        {job.ended_at ? ` · ended ${formatDisplay(job.ended_at, tz)}` : ""}
      </div>
      {running && (count != null || job.phase) && (
        <div style={{ marginTop: 8 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              fontSize: 13,
              color: "var(--c-ink-2)",
              marginBottom: 4,
            }}
          >
            <span className="mono">
              {job.phase ?? "working"}
              {count != null
                ? total != null && total > 0
                  ? ` · ${count.toLocaleString()} / ≈${total.toLocaleString()}`
                  : ` · ${count.toLocaleString()} loaded`
                : ""}
            </span>
            <span className="mono">
              {pct != null ? `${Math.floor(pct * 100)}%` : ""}
              {etaSeconds != null ? ` · ${formatDuration(etaSeconds)} left` : ""}
            </span>
          </div>
          {/* Determinate bar when we have a total to divide by; otherwise an
              indeterminate sweep so the admin still sees "it's alive". */}
          <div
            style={{
              height: 6,
              borderRadius: 999,
              background: "var(--c-blue-100)",
              overflow: "hidden",
            }}
          >
            {pct != null ? (
              <div
                style={{
                  width: `${Math.min(100, Math.max(2, pct * 100))}%`,
                  height: "100%",
                  background: "var(--c-blue-600)",
                  transition: "width 0.4s ease",
                }}
              />
            ) : (
              <div className="indeterminate-sweep" style={{ height: "100%" }} />
            )}
          </div>
        </div>
      )}
      {buildingsIngestedCount != null && (
        <div className="mono" style={{ fontSize: 14, color: "var(--c-ink)", marginTop: 6 }}>
          {buildingsIngestedCount.toLocaleString()} buildings ingested
        </div>
      )}
      {job.error && (
        <div
          style={{
            fontSize: 14,
            color: "var(--c-danger)",
            marginTop: 6,
            wordBreak: "break-word",
          }}
        >
          {job.error}
        </div>
      )}
    </div>
  );
}
