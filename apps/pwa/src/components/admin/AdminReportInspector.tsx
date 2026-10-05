import { memo, useEffect, useRef, useState } from "react";
import { type VerifyReportResult, getAdminReportDetail, verifyAdminReport } from "../../api/admin";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import { formatDisplay } from "../../lib/adminTimezone";
import type {
  AdminReportDamageClass,
  AdminReportDetail,
  AdminReportTranslationStatus,
  AdminReporterStats,
} from "../../types/admin";
import { InfoTooltip } from "./dashboard/InfoTooltip";
import { Icon } from "./icons";

interface Props {
  reportId: string | null;
}

function damageClassChip(d: AdminReportDamageClass): string {
  if (d === "complete") return "chip danger";
  if (d === "partial") return "chip warn";
  return "chip safe";
}

function damageClassLabel(d: AdminReportDamageClass): string {
  if (d === "complete") return "● complete";
  if (d === "partial") return "● partial";
  return "● minimal";
}

// Crisis type arrives as a DB enum (e.g. "natural_hazards"); de-underscore so
// the raw value never shows in the inspector.
function crisisTypeLabel(type: string): string {
  if (type === "natural_hazards") return "Natural hazards";
  if (type === "technological") return "Technological";
  if (type === "human_made") return "Human-made";
  return type.replace(/_/g, " ");
}

function fmtCoord(loc: { lat: number; lng: number } | null): string {
  if (!loc) return "—";
  return `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)}`;
}

function AdminReportInspectorImpl({ reportId }: Props) {
  const tz = useAdminTimezone();
  const [detail, setDetail] = useState<AdminReportDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [verifyResult, setVerifyResult] = useState<VerifyReportResult | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  // Auto-recovery budget for the signed photo URL (see the <img> onError),
  // reset when a different report is selected.
  const photoRetryRef = useRef(0);
  const photoRetryForRef = useRef<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reset verify state whenever selected report changes
  useEffect(() => {
    setVerifyResult(null);
    setVerifyError(null);
  }, [reportId]);

  const handleVerify = async (verified: boolean) => {
    if (!reportId) return;
    setVerifying(true);
    setVerifyError(null);
    try {
      setVerifyResult(await verifyAdminReport(reportId, verified));
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Verify failed");
    } finally {
      setVerifying(false);
    }
  };

  useEffect(() => {
    // Referenced so biome treats it as a dependency: bumping it forces a refetch.
    void refreshTick;
    if (!reportId) {
      setDetail(null);
      setError(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getAdminReportDetail(reportId, { signal: ctrl.signal })
      .then((d) => {
        setDetail(d);
        setLoading(false);
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [reportId, refreshTick]);

  if (!reportId) {
    return (
      <div style={panelStyle}>
        <div className="label-eyebrow" style={{ marginBottom: 6 }}>
          Inspector
        </div>
        <div style={{ fontSize: 12, color: "var(--c-ink-3)", lineHeight: 1.5 }}>
          Click a report on the map or in the list to see the photo, full form answers, and the
          resolved building.
        </div>
      </div>
    );
  }

  if (loading && !detail) {
    return (
      <div style={panelStyle}>
        <div className="label-eyebrow" style={{ marginBottom: 6 }}>
          Inspector
        </div>
        <div style={{ fontSize: 12, color: "var(--c-ink-3)" }}>Loading…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={panelStyle}>
        <div className="label-eyebrow" style={{ marginBottom: 6 }}>
          Inspector
        </div>
        <div
          style={{
            background: "var(--c-danger-bg)",
            color: "var(--c-danger)",
            border: "1px solid var(--c-danger)",
            borderRadius: 8,
            padding: "10px 12px",
            fontSize: 12,
            marginBottom: 10,
          }}
        >
          {error}
        </div>
        <button
          type="button"
          className="btn secondary"
          onClick={() => setRefreshTick((n) => n + 1)}
        >
          Retry
        </button>
      </div>
    );
  }

  if (!detail) return null;

  const usingBuildingCentroid = detail.location === null && detail.building_centroid !== null;
  const isAiGeocoded = detail.location_source === "ai_geocode";

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <div className="label-eyebrow">Inspector</div>
        <span className="mono" style={{ fontSize: 10, color: "var(--c-ink-3)" }}>
          {detail.id.slice(0, 8)}
        </span>
      </div>

      <div
        style={{
          position: "relative",
          width: "100%",
          // Natural aspect ratio (no crop); the 4:3 frame is only for the placeholder.
          ...(detail.photo_url ? null : { aspectRatio: "4 / 3" }),
          background: "var(--c-line-2)",
          borderRadius: 8,
          overflow: "hidden",
          marginBottom: 12,
        }}
      >
        {detail.photo_url ? (
          // The signed URL expires after ~15 min: on load failure, silently
          // re-fetch the detail to mint a fresh one. Capped so a broken photo
          // can't loop; a successful load resets the budget.
          <img
            src={detail.photo_url}
            alt={`Report ${detail.id.slice(0, 8)}`}
            style={{ width: "100%", height: "auto", display: "block" }}
            onError={() => {
              // Fresh budget when the selected report changed.
              if (photoRetryForRef.current !== detail.id) {
                photoRetryForRef.current = detail.id;
                photoRetryRef.current = 0;
              }
              if (photoRetryRef.current >= 2) return;
              photoRetryRef.current += 1;
              setRefreshTick((n) => n + 1);
            }}
            onLoad={() => {
              photoRetryForRef.current = detail.id;
              photoRetryRef.current = 0;
            }}
          />
        ) : (
          // Description-only report: placeholder instead of a broken-image icon.
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              color: "var(--c-ink-3)",
              fontSize: 12,
            }}
          >
            <Icon.camera width="20" height="20" />
            <span>No photo (description-only report)</span>
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <ConfidencePill
          score={
            // Backfilled rows can carry relevance only, with a meaningless
            // default score of 0 and computed_at null. Show a stored score only
            // when the report was scored (computed_at) or verified (which floors
            // confidence but leaves computed_at null); a verify in this session
            // wins via verifyResult.
            verifyResult?.confidence_score ??
            (detail.quality?.computed_at || detail.verified ? detail.confidence_score : null)
          }
        />
        <RelevanceBadge label={detail.quality?.relevance_label ?? null} />
        <DuplicateImageBadge duplicate={detail.quality?.is_duplicate_image ?? false} />
        <span style={{ fontSize: 11, color: "var(--c-ink-3)" }}>
          {formatDisplay(detail.created_at, tz)}
        </span>
      </div>

      <VerificationBanner
        verified={verifyResult?.verified ?? detail.verified ?? false}
        verifying={verifying}
        verifyError={verifyError}
        onVerify={handleVerify}
      />

      <Field label="Severity">
        <span className={damageClassChip(detail.damage_class)} style={{ fontSize: 11 }}>
          {damageClassLabel(detail.damage_class)}
        </span>
      </Field>
      <Field label="Infra type">
        {detail.infra_type && detail.infra_type.length > 0 ? detail.infra_type.join(", ") : "—"}
      </Field>
      <Field label="Infra name">{detail.infra_name ?? "—"}</Field>
      <Field label="Description">
        <DescriptionWithTranslation
          raw={detail.description ?? null}
          translatedText={detail.description_en}
          lang={detail.description_lang}
          status={detail.description_status}
        />
      </Field>
      <AiCaptionField caption={detail.ai_caption} status={detail.ai_caption_status} />
      <Field label="Reported crisis nature">
        {[
          detail.crisis_type ? crisisTypeLabel(detail.crisis_type) : null,
          detail.crisis_type_detailed,
        ]
          .filter(Boolean)
          .join(" · ") || "—"}
      </Field>
      <Field label="Debris">{detail.debris ?? "—"}</Field>

      <Field label="Location">
        {detail.location ? (
          <span className="mono" style={{ fontSize: 12 }}>
            {fmtCoord(detail.location)}
          </span>
        ) : usingBuildingCentroid ? (
          <span style={{ fontSize: 12, color: "var(--c-ink-2)" }}>
            <span className="mono">{fmtCoord(detail.building_centroid)}</span>
            <span style={{ color: "var(--c-ink-3)", marginInlineStart: 6 }}>
              (estimated from building)
            </span>
          </span>
        ) : isAiGeocoded ? (
          <span style={{ fontSize: 12, color: "var(--c-ink-3)" }}>AI-estimated (see below)</span>
        ) : (
          "—"
        )}
      </Field>

      {/* The AI geocode was inferred from these typed directions; show them so
          the coordinator can judge the placement. */}
      {isAiGeocoded && detail.route_description ? (
        <Field label="Located from">
          <DescriptionWithTranslation
            raw={detail.route_description}
            translatedText={detail.route_description_en}
            lang={detail.route_description_lang}
            status={detail.route_description_status}
          />
        </Field>
      ) : null}

      <Field label="Building">
        {detail.building_id ? (
          // Prefer Overture's own footprint name; fall back to the citizen's
          // infra name (most footprints are unnamed). The raw UUID stays in
          // the tooltip for traceability, off the visible row.
          <span title={detail.building_id}>
            {detail.building_name ?? detail.infra_name ?? "Unnamed building"}
          </span>
        ) : (
          "—"
        )}
      </Field>

      <SubmitterSection
        clientId={detail.client_id ?? null}
        stats={verifyResult?.reporter_stats ?? detail.reporter_stats ?? null}
      />
    </div>
  );
}

// Memoised: the parent re-renders on every map pan, but the inspector only
// depends on its props.
export const AdminReportInspector = memo(AdminReportInspectorImpl);

// Confidence-score inputs for the tooltip. Mirrors the worker's weights in
// plain wording, not exact percentages.
const CONFIDENCE_FACTORS: readonly string[] = [
  "Whether AI judged the photo relevant to the crisis",
  "Whether AI's damage read matches the citizen's",
  "How recent the photo is",
  "Whether the photo's GPS matches the dropped pin",
  "Whether other people reported the same area",
  "Whether the photo is a duplicate of one already submitted",
];

function ConfidencePill({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  const pct = Math.round(score * 100);
  const dots = Math.ceil(score * 4);
  const color = score >= 0.7 ? "var(--c-safe)" : score >= 0.4 ? "var(--c-warn)" : "var(--c-ink-3)";
  const band = score >= 0.7 ? "High" : score >= 0.4 ? "Medium" : "Low";

  const tooltip = (
    <span style={{ display: "block" }}>
      <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <b>Confidence score</b>
        <span style={{ fontWeight: 700, color }}>
          {pct}% · {band}
        </span>
      </span>
      <span style={{ display: "block", marginTop: 5, opacity: 0.9 }}>
        How likely this report is accurate. It blends:
      </span>
      <span style={{ display: "block", marginTop: 6 }}>
        {CONFIDENCE_FACTORS.map((f) => (
          <span key={f} style={{ display: "flex", gap: 6, marginTop: 3, lineHeight: 1.4 }}>
            <span aria-hidden="true" style={{ opacity: 0.6 }}>
              •
            </span>
            <span>{f}</span>
          </span>
        ))}
      </span>
      <span
        style={{
          display: "block",
          marginTop: 8,
          paddingTop: 7,
          borderTop: "1px solid color-mix(in srgb, currentColor 22%, transparent)",
          opacity: 0.8,
        }}
      >
        A coordinator marking the report Verified by UNDP overrides this score.
      </span>
    </span>
  );

  return (
    <InfoTooltip content={tooltip} surface>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 7px",
          borderRadius: 999,
          border: `1px solid ${color}`,
          fontSize: 10,
          color,
          fontWeight: 600,
          cursor: "help",
        }}
      >
        {[1, 2, 3, 4].map((i) => (
          <span
            key={i}
            style={{
              width: 5,
              height: 5,
              borderRadius: 999,
              background: i <= dots ? color : "var(--c-line)",
            }}
          />
        ))}
        Confidence {pct}%
      </span>
    </InfoTooltip>
  );
}

// AI photo-relevance verdict next to the confidence pill. "irrelevant" is a
// loud red flag (a signal, not a rejection); "relevant" is deliberately quiet
// so it doesn't dilute the red flag. "unclear" and unscored show nothing.
function RelevanceBadge({ label }: { label: string | null }) {
  if (label !== "relevant" && label !== "irrelevant") return null;
  const irrelevant = label === "irrelevant";

  return (
    <InfoTooltip
      surface
      content={
        <span style={{ display: "block" }}>
          <b>{irrelevant ? "Photo flagged by AI" : "Photo checked by AI"}</b>
          <span style={{ display: "block", marginTop: 5, opacity: 0.9 }}>
            {irrelevant
              ? "AI reviewed this photo and judged it not relevant to the crisis. Worth a closer look before relying on it."
              : "AI reviewed this photo and judged it relevant to the crisis."}
          </span>
        </span>
      }
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 7px",
          borderRadius: 999,
          border: irrelevant
            ? "1px solid var(--c-danger)"
            : "1px solid color-mix(in srgb, var(--c-safe) 30%, transparent)",
          background: irrelevant ? "var(--c-danger-bg)" : "var(--c-safe-bg)",
          color: irrelevant ? "var(--c-danger)" : "var(--c-safe)",
          fontSize: 10,
          fontWeight: irrelevant ? 700 : 600,
          cursor: "help",
        }}
      >
        {irrelevant ? <Icon.alert width="11" height="11" /> : <Icon.check width="11" height="11" />}
        {irrelevant ? "Irrelevant photo" : "Relevant photo"}
      </span>
    </InfoTooltip>
  );
}

// The photo is byte-identical to one already submitted (content-addressed
// storage). A soft triage signal, not a rejection: the image is not novel evidence.
function DuplicateImageBadge({ duplicate }: { duplicate: boolean }) {
  if (!duplicate) return null;

  return (
    <InfoTooltip
      surface
      content={
        <span style={{ display: "block" }}>
          <b>Duplicate image detected</b>
          <span style={{ display: "block", marginTop: 5, opacity: 0.9 }}>
            This exact photo was already submitted with an earlier report. It may be genuine
            corroboration or a re-used image; worth a look before treating it as new evidence.
          </span>
        </span>
      }
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 7px",
          borderRadius: 999,
          border: "1px solid color-mix(in srgb, var(--c-warn) 35%, transparent)",
          background: "var(--c-warn-bg)",
          color: "var(--c-warn)",
          fontSize: 10,
          fontWeight: 600,
          cursor: "help",
        }}
      >
        <Icon.copy width="11" height="11" />
        Duplicate image
      </span>
    </InfoTooltip>
  );
}

// "Verified by UNDP" is a sign-off on the report as a whole, not on one field.
const VERIFY_TOOLTIP = (
  <span style={{ display: "block" }}>
    <b>UNDP verification</b>
    <span style={{ display: "block", marginTop: 5, opacity: 0.9 }}>
      A coordinator's official sign-off on the whole report: that the photo, the damage severity,
      and the location together reflect real, accurate damage, not just one of them.
    </span>
    <span style={{ display: "block", marginTop: 6, opacity: 0.9 }}>
      It overrides the automatic confidence score and earns the reporter a Field Verified badge.
    </span>
  </span>
);

interface VerificationBannerProps {
  verified: boolean;
  verifying: boolean;
  verifyError: string | null;
  onVerify: (verified: boolean) => void;
}

// Verification floors the confidence score high and earns the reporter a
// "Field Verified" badge.
function VerificationBanner({
  verified,
  verifying,
  verifyError,
  onVerify,
}: VerificationBannerProps) {
  const accent = verified ? "var(--c-safe)" : "var(--c-ink-3)";
  return (
    <div style={{ marginBottom: 14 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "10px 12px",
          borderRadius: 8,
          border: `1px solid ${verified ? "var(--c-safe)" : "var(--c-line)"}`,
          background: verified ? "var(--c-safe-bg, rgba(34,160,94,0.08))" : "var(--c-line-2)",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: accent }}>
          <Icon.shield width="18" height="18" />
          <span style={{ display: "inline-flex", flexDirection: "column", lineHeight: 1.25 }}>
            <strong style={{ fontSize: 13, display: "inline-flex", alignItems: "center", gap: 5 }}>
              {verified ? "Verified by UNDP" : "Not verified by UNDP"}
              <InfoTooltip surface content={VERIFY_TOOLTIP} />
            </strong>
            <span style={{ fontSize: 11, color: "var(--c-ink-3)" }}>
              {verified
                ? "A coordinator reviewed the whole report and confirmed it's genuine"
                : "No coordinator has reviewed this report yet"}
            </span>
          </span>
        </span>
        <button
          type="button"
          onClick={() => onVerify(!verified)}
          disabled={verifying}
          className="btn secondary"
          style={{ fontSize: 12, padding: "6px 14px", whiteSpace: "nowrap" }}
        >
          {verifying ? "…" : verified ? "Unverify" : "Verify"}
        </button>
      </div>
      {verifyError && (
        <div style={{ marginTop: 6, fontSize: 11, color: "var(--c-ink-3)", fontStyle: "italic" }}>
          {verifyError}
        </div>
      )}
    </div>
  );
}

// "Submitter" is the citizen's anonymous per-device client id; the expandable
// body is that submitter's track record across all their reports.
function SubmitterSection({
  clientId,
  stats,
}: {
  clientId: string | null;
  stats: AdminReporterStats | null | undefined;
}) {
  const [open, setOpen] = useState(false);

  if (!clientId && !stats) return null;

  const idLabel = clientId ? clientId.slice(0, 8) : "anonymous";

  const heading = (
    <span style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
      <span style={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Submitter
      </span>
      <span
        className="mono"
        style={{
          fontSize: 11,
          fontWeight: 400,
          color: "var(--c-ink-3)",
          textTransform: "none",
          letterSpacing: 0,
        }}
      >
        {idLabel}
      </span>
    </span>
  );

  const wrapStyle: React.CSSProperties = {
    marginTop: 16,
    borderTop: "1px solid var(--c-line)",
    paddingTop: 12,
  };

  // No track record (e.g. anonymous submitter): static row, no disclosure.
  if (!stats) {
    return <div style={{ ...wrapStyle, fontSize: 12, color: "var(--c-blue-700)" }}>{heading}</div>;
  }

  return (
    <div style={wrapStyle}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: 0,
          fontSize: 12,
          color: "var(--c-blue-700)",
          width: "100%",
          justifyContent: "space-between",
        }}
      >
        {heading}
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          aria-hidden="true"
          style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 0.2s" }}
        >
          <path
            d="M2 4l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          <Row label="Total reports" value={String(stats.total_reports)} />
          <Row label="Quality reports" value={String(stats.quality_reports)} />
          <Row
            label="Badges"
            value={stats.badge_count > 0 ? `${stats.badge_count} earned` : "None yet"}
          />
          <Row label="UNDP verified" value={String(stats.verified_count)} />
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
      <span style={{ color: "var(--c-ink-3)" }}>{label}</span>
      <span style={{ color: "var(--c-ink)", fontWeight: 500 }}>{value}</span>
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  borderInlineStart: "1px solid var(--c-line)",
  background: "var(--c-card)",
  padding: 18,
  overflowY: "auto",
};

// AI vision caption. Hidden for `skipped` (no photo) and null status; `pending`
// and `failed` show quiet markers without internal failure detail.
function AiCaptionField({ caption, status }: { caption: string | null; status: string | null }) {
  if (caption) {
    return (
      <Field label="AI photo caption">
        <span style={{ fontStyle: "italic", color: "var(--c-ink-2)" }}>{caption}</span>
      </Field>
    );
  }
  if (status === "pending") {
    return (
      <Field label="AI photo caption">
        <span style={{ fontSize: 11, color: "var(--c-ink-3)", fontStyle: "italic" }}>
          captioning…
        </span>
      </Field>
    );
  }
  if (status === "failed") {
    // Not actionable, so quiet rather than alarming, but distinct from `pending`.
    return (
      <Field label="AI photo caption">
        <span style={{ fontSize: 11, color: "var(--c-ink-3)", fontStyle: "italic" }}>
          No caption available
        </span>
      </Field>
    );
  }
  return null;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        className="label-eyebrow"
        style={{ fontSize: 12, marginBottom: 4, color: "var(--c-blue-700)" }}
      >
        {label}
      </div>
      <div style={{ fontSize: 13, color: "var(--c-ink)" }}>{children}</div>
    </div>
  );
}

// Raw citizen text plus the English translation when they differ (passthrough
// rows have `description_en === description`, so the duplicate is suppressed).
// Pending/failed translations show a small inline marker.
function DescriptionWithTranslation({
  raw,
  translatedText,
  lang,
  status,
}: {
  raw: string | null;
  translatedText: string | null;
  lang: string | null;
  status: AdminReportTranslationStatus | null;
}) {
  if (!raw) return <>—</>;

  const showTranslation =
    status === "ready" && lang && lang !== "en" && translatedText && translatedText !== raw;

  return (
    <>
      <div>{raw}</div>
      {showTranslation ? (
        <div
          style={{
            marginTop: 6,
            paddingInlineStart: 8,
            borderInlineStart: "2px solid var(--c-line)",
            color: "var(--c-ink-2)",
            fontSize: 12,
          }}
        >
          <span
            className="label-eyebrow"
            style={{ fontSize: 10, color: "var(--c-ink-3)", marginInlineEnd: 6 }}
          >
            EN{lang ? ` · from ${lang}` : ""}
          </span>
          {translatedText}
        </div>
      ) : status === "pending" ? (
        <div style={{ marginTop: 4, fontSize: 11, color: "var(--c-ink-3)", fontStyle: "italic" }}>
          translating…
        </div>
      ) : status === "failed" ? (
        <div style={{ marginTop: 4, fontSize: 11, color: "var(--c-danger)" }}>
          translation failed
        </div>
      ) : null}
    </>
  );
}
