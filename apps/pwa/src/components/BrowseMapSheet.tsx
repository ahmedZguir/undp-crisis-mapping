// Bottom sheet for BrowseMap; BrowseMap owns the selection state.
import type { TFunction } from "i18next";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useRef, useState } from "react";
import type { CrisisStats, PublicReportDetail } from "../api/reports";
import { useGatedImageSrc } from "../hooks/useGatedImageSrc";

export interface HeatFeatureProperties {
  cell_id: string;
  report_count: number;
  minimal_count: number;
  partial_count: number;
  complete_count: number;
  weighted_severity: number;
  latest_at: string;
}

// Shared by every map circle layer and the detail card so colours match on dot and panel.
export const DAMAGE_COLORS: Record<string, string> = {
  minimal: "#16a34a",
  partial: "#f59e0b",
  complete: "#dc2626",
};
export const FALLBACK_DAMAGE_COLOR = "#2563eb";
const damageColor = (dc?: string | null): string =>
  (dc && DAMAGE_COLORS[dc]) || FALLBACK_DAMAGE_COLOR;

export type SelectedFeature =
  | { kind: "hex"; props: HeatFeatureProperties }
  | { kind: "building"; damageClass: string; reportCount: number }
  | {
      kind: "mine";
      damageClass: string;
      photoUrl?: string | null;
    }
  | { kind: "full-loading" }
  | { kind: "full"; detail: PublicReportDetail }
  | { kind: "full-error"; reason: "gone" | "error" };

// Pin/report taps rendered as a card; hex taps drive the classification bar.
type TappedFeature = Exclude<SelectedFeature, { kind: "hex" }>;

// Peek height: tall enough that the bar and legend clear the bottom tab bar.
const PEEK_HEIGHT = 124;

export function BottomSheet({
  crisisName,
  stats,
  unavailable,
  loading,
  open,
  onOpenChange,
  selected,
  onClearSelected,
  t,
}: {
  crisisName: string | null;
  stats: CrisisStats | null;
  unavailable: boolean;
  loading: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: SelectedFeature | null;
  onClearSelected: () => void;
  t: TFunction;
}) {
  // A tapped hex shows its area distribution; otherwise crisis-wide totals.
  const hexSelected = selected?.kind === "hex" ? selected.props : null;
  const by = stats?.by_damage_class;
  const breakdown = hexSelected
    ? {
        minimal: hexSelected.minimal_count,
        partial: hexSelected.partial_count,
        complete: hexSelected.complete_count,
      }
    : (by ?? { minimal: 0, partial: 0, complete: 0 });
  const sum = breakdown.minimal + breakdown.partial + breakdown.complete;
  const pct = (n: number) => (sum > 0 ? Math.round((n / sum) * 100) : 0);
  const severePct = pct(breakdown.complete);
  const partialPct = pct(breakdown.partial);
  const minorPct = sum > 0 ? Math.max(0, 100 - severePct - partialPct) : 0;
  const last24 = stats?.last_24h ?? 0;

  const sheetRef = useRef<HTMLDivElement>(null);
  const [dragY, setDragY] = useState<number | null>(null);
  const dragStartY = useRef(0);
  const dragStartOffset = useRef(0);

  const collapsedOffset = () => {
    const h = sheetRef.current?.offsetHeight ?? 0;
    return Math.max(0, h - PEEK_HEIGHT);
  };

  const baseOffset = open ? 0 : collapsedOffset();
  const translateY = dragY ?? baseOffset;

  const onPointerDown = (e: ReactPointerEvent) => {
    dragStartY.current = e.clientY;
    dragStartOffset.current = baseOffset;
    setDragY(baseOffset);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    if (dragY == null) return;
    const dy = e.clientY - dragStartY.current;
    const max = collapsedOffset();
    const next = Math.min(max, Math.max(0, dragStartOffset.current + dy));
    setDragY(next);
  };
  const onPointerUp = () => {
    if (dragY == null) return;
    const max = collapsedOffset();
    onOpenChange(dragY < max / 2);
    setDragY(null);
  };

  return (
    <div
      ref={sheetRef}
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        // TabBar publishes the var and owns the safe-area inset; 0 if absent.
        bottom: "var(--undp-tabbar-h, 0px)",
        background: "var(--c-card)",
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        maxHeight: "58%",
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 -10px 28px rgba(8,40,80,0.18)",
        overflow: "hidden",
        // Below the tab bar (z1400) so it stays on top; above the map controls (z5).
        zIndex: 1390,
        transform: `translateY(${translateY}px)`,
        transition: dragY == null ? "transform 220ms cubic-bezier(0.32, 0.72, 0, 1)" : "none",
        touchAction: "none",
      }}
    >
      <button
        type="button"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={() => {
          if (dragY == null) onOpenChange(!open);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpenChange(!open);
          }
        }}
        aria-label={open ? t("browseMap.collapse") : t("browseMap.expand")}
        aria-expanded={open}
        style={{
          padding: "10px 18px 6px",
          cursor: "grab",
          touchAction: "none",
          background: "transparent",
          border: 0,
          width: "100%",
          display: "block",
          textAlign: "start",
        }}
      >
        <div
          style={{
            width: 44,
            height: 4,
            borderRadius: 2,
            background: "var(--c-line)",
            margin: "0 auto 8px",
          }}
        />
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span
            style={{
              fontSize: 16,
              fontWeight: 800,
              color: "var(--c-ink)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              minWidth: 0,
            }}
          >
            {hexSelected
              ? t(
                  hexSelected.report_count === 1
                    ? "browseMap.areaReports_one"
                    : "browseMap.areaReports_other",
                  {
                    count: hexSelected.report_count,
                    defaultValue:
                      hexSelected.report_count === 1
                        ? "1 report"
                        : `${hexSelected.report_count} reports`,
                  },
                )
              : (crisisName ?? t("browseMap.crisisFallback"))}
          </span>
          {!hexSelected && (
            <span
              style={{
                fontSize: 11,
                color: "var(--c-ink-3)",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                flexShrink: 0,
              }}
            >
              {t("browseMap.crisis")}
            </span>
          )}
          {!hexSelected && (
            <svg
              aria-hidden="true"
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{
                marginInlineStart: "auto",
                color: "var(--c-ink-3)",
                flexShrink: 0,
                transform: open ? "rotate(180deg)" : "none",
                transition: "transform 200ms",
              }}
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          )}
        </div>
      </button>

      {/* Outside the grip button: buttons cannot nest. */}
      {hexSelected && (
        <button
          type="button"
          onClick={onClearSelected}
          aria-label={t("browseMap.detailClose", { defaultValue: "Close" })}
          style={{
            position: "absolute",
            top: 14,
            insetInlineEnd: 14,
            width: 28,
            height: 28,
            border: 0,
            background: "transparent",
            color: "var(--c-ink-3)",
            fontSize: 20,
            lineHeight: 1,
            cursor: "pointer",
            display: "grid",
            placeItems: "center",
            zIndex: 1,
          }}
        >
          ×
        </button>
      )}

      {unavailable ? (
        <div style={{ padding: "16px 18px 26px", textAlign: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--c-danger)" }}>
            {t("browseMap.crisisUnavailableHeading")}
          </div>
          <div style={{ fontSize: 12, color: "var(--c-ink-3)", marginTop: 4 }}>
            {t("browseMap.crisisUnavailable")}
          </div>
        </div>
      ) : !selected && sum === 0 && !loading ? (
        <div style={{ padding: "16px 18px 26px", textAlign: "center" }}>
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 999,
              background: "var(--c-blue-100)",
              color: "var(--c-blue-700)",
              display: "grid",
              placeItems: "center",
              margin: "0 auto 10px",
            }}
          >
            <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path
                d="M3 7l6-3 6 3 6-3v13l-6 3-6-3-6 3V7z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{t("browseMap.noReportsYet")}</div>
          <div style={{ fontSize: 12, color: "var(--c-ink-3)", marginTop: 4 }}>
            {t("browseMap.beTheFirst")}
          </div>
        </div>
      ) : (
        <>
          <div style={{ padding: "2px 18px 12px" }}>
            <div
              style={{
                display: "flex",
                height: 10,
                borderRadius: 999,
                overflow: "hidden",
                background: "var(--c-line-2)",
              }}
            >
              <div style={{ width: `${severePct}%`, background: "var(--c-danger)" }} />
              <div style={{ width: `${partialPct}%`, background: "var(--c-warn)" }} />
              <div style={{ width: `${minorPct}%`, background: "var(--c-safe)" }} />
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: 8,
                fontSize: 11,
                color: "var(--c-ink-3)",
              }}
            >
              {(
                [
                  ["var(--c-danger)", "browseMap.severe", severePct],
                  ["var(--c-warn)", "browseMap.partial", partialPct],
                  ["var(--c-safe)", "browseMap.minor", minorPct],
                ] as const
              ).map(([color, labelKey, pct]) => (
                <span
                  key={labelKey}
                  style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
                >
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
                  {t(labelKey)}
                  <b style={{ color: "var(--c-ink)", marginInlineStart: 3 }}>{pct}%</b>
                </span>
              ))}
            </div>
          </div>

          <div style={{ padding: "0 18px 22px", overflow: "auto" }}>
            {!hexSelected && (
              <div style={{ marginTop: 12, fontSize: 11, color: "var(--c-ink-3)" }}>
                <b style={{ color: "var(--c-danger)", fontWeight: 800 }}>+{last24}</b>{" "}
                {t("browseMap.last24h")}
              </div>
            )}

            {selected && selected.kind !== "hex" && (
              <SelectedCard selected={selected} t={t} onClose={onClearSelected} />
            )}

            <div
              style={{
                marginTop: 14,
                fontSize: 11,
                color: "var(--c-ink-3)",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <svg aria-hidden="true" width="13" height="13" viewBox="0 0 16 16" fill="none">
                <path
                  d="M8 2l5 2v4c0 3-2.2 5.4-5 6-2.8-.6-5-3-5-6V4l5-2z"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinejoin="round"
                />
              </svg>
              {t("browseMap.privacy")}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SelectedCard({
  selected,
  t,
  onClose,
}: {
  selected: TappedFeature;
  t: TFunction;
  onClose: () => void;
}) {
  return (
    <div
      style={{
        position: "relative",
        marginTop: 12,
        padding: "12px 36px 12px 14px",
        border: "1px solid var(--c-line)",
        borderRadius: 12,
        background: "var(--c-blue-50)",
        fontSize: 13,
        lineHeight: 1.5,
        color: "var(--c-ink-2)",
        textAlign: "start",
      }}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label={t("browseMap.detailClose", { defaultValue: "Close" })}
        style={{
          position: "absolute",
          top: 6,
          insetInlineEnd: 6,
          width: 26,
          height: 26,
          border: 0,
          background: "transparent",
          color: "var(--c-ink-3)",
          fontSize: 18,
          lineHeight: 1,
          cursor: "pointer",
        }}
      >
        ×
      </button>
      <SelectedBody selected={selected} t={t} />
    </div>
  );
}

function DamageHeadline({
  damageClass,
  t,
}: {
  damageClass: string;
  t: TFunction;
}) {
  const label = t(`damage.${damageClass}`);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 800, fontSize: 14 }}>
      <span
        aria-hidden
        style={{
          width: 12,
          height: 12,
          borderRadius: 999,
          flexShrink: 0,
          background: damageColor(damageClass),
          border: "2px solid #fff",
          boxShadow: "0 0 0 1px var(--c-line)",
        }}
      />
      <span style={{ color: "var(--c-ink)" }}>
        {t("browseMap.buildingsPopupDamage", { damage: label })}
      </span>
    </div>
  );
}

function SelectedBody({
  selected,
  t,
}: {
  selected: TappedFeature;
  t: TFunction;
}) {
  if (selected.kind === "full-loading") {
    return <div style={{ color: "var(--c-ink-3)" }}>{t("browseMap.fullPopupLoading")}</div>;
  }
  if (selected.kind === "full-error") {
    return (
      <div style={{ color: "var(--c-ink-3)" }}>
        {t(
          selected.reason === "gone"
            ? "browseMap.fullPopupNoLongerVisible"
            : "browseMap.fullPopupError",
        )}
      </div>
    );
  }
  if (selected.kind === "full") {
    const { detail } = selected;
    return (
      <div>
        {detail.photo_url && <ReportPhoto url={detail.photo_url} t={t} />}
        <DamageHeadline damageClass={detail.damage_class} t={t} />
      </div>
    );
  }
  if (selected.kind === "building") {
    return (
      <div>
        <DamageHeadline damageClass={selected.damageClass} t={t} />
        <div style={{ marginTop: 4, color: "var(--c-ink-3)", fontSize: 12 }}>
          {t(
            selected.reportCount === 1
              ? "browseMap.buildingsPopupReports_one"
              : "browseMap.buildingsPopupReports_other",
            { count: selected.reportCount },
          )}
        </div>
      </div>
    );
  }
  return (
    <div>
      <div
        style={{
          fontWeight: 800,
          fontSize: 13,
          color: "var(--c-blue-800)",
          marginBottom: 6,
        }}
      >
        {t("browseMap.yourReport")}
      </div>
      {selected.photoUrl && <ReportPhoto url={selected.photoUrl} t={t} />}
      <DamageHeadline damageClass={selected.damageClass} t={t} />
    </div>
  );
}

// Falls back to "unavailable" when the signed URL is stale.
function ReportPhoto({
  url,
  t,
}: {
  url: string;
  t: TFunction;
}) {
  const [failed, setFailed] = useState(false);
  // Native behind the ngrok gate: <img> cannot carry the credential, so fetch to a blob: URL.
  const photo = useGatedImageSrc(url, 0, () => setFailed(true));
  if (failed || photo.failed) {
    return (
      <div style={{ color: "var(--c-ink-3)", fontSize: 11, marginBottom: 6 }}>
        {t("browseMap.fullPopupPhotoUnavailable")}
      </div>
    );
  }
  if (!photo.src) return null; // native: blob fetch in flight
  return (
    <img
      src={photo.src}
      alt={t("browseMap.fullPopupPhotoAlt")}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{
        display: "block",
        width: "100%",
        maxHeight: 160,
        objectFit: "contain",
        borderRadius: 8,
        marginBottom: 8,
      }}
    />
  );
}
