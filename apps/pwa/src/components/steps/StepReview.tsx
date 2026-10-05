import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useObjectUrl } from "../../hooks/useObjectUrl";
import { PHOTO_GPS_DIVERGENCE_M, metersBetween } from "../../lib/photoMetadata";
import type { ReportContentStatus } from "../../lib/reportValidation";
import type { FormPage, FormState } from "../../types";
import { StepShell } from "../StepShell";
import { buildReviewRows } from "./reviewRows";

interface StepReviewProps {
  formState: FormState;
  online: boolean;
  onEdit: (targetStep: number) => void;
  onBack: () => void;
  onSubmit: () => Promise<void> | void;
  stepNumber?: number;
  totalSteps?: number;
  crisisName: string | null;
  // Edit jumps use the page's index in this list.
  visiblePages: FormPage[];
  // Minimum-content gate; see lib/reportValidation.ts.
  content: ReportContentStatus;
}

function PencilIcon() {
  return (
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
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  );
}

interface RowProps {
  label: string;
  value: string;
  onEdit: () => void;
  editAriaLabel: string;
}

function Row({ label, value, onEdit, editAriaLabel }: RowProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 0",
        borderBottom: "1px solid var(--c-line)",
        fontSize: 13,
      }}
    >
      <span
        style={{
          color: "var(--c-ink-3)",
          fontWeight: 600,
          flexShrink: 0,
          minWidth: 80,
        }}
      >
        {label}
      </span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          color: "var(--c-ink)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
        title={value}
      >
        {value}
      </span>
      <button
        type="button"
        onClick={onEdit}
        aria-label={editAriaLabel}
        style={{
          flexShrink: 0,
          // 44×44 minimum touch target (WCAG 2.5.5 / Apple HIG).
          width: 44,
          height: 44,
          border: "none",
          background: "none",
          padding: 0,
          color: "var(--c-blue-700, #1d4ed8)",
          cursor: "pointer",
          display: "grid",
          placeItems: "center",
        }}
      >
        <PencilIcon />
      </button>
    </div>
  );
}

export function StepReview({
  formState,
  online,
  onEdit,
  onBack,
  onSubmit,
  stepNumber,
  totalSteps,
  crisisName,
  visiblePages,
  content,
}: StepReviewProps) {
  const { t } = useTranslation();
  const photoUrl = useObjectUrl(formState.photo);
  const [submitting, setSubmitting] = useState(false);

  // Photo + damage live on the locked first page; description may be disabled by the admin.
  const locationStep = visiblePages.findIndex((p) => p.kind === "location");
  const descriptionStep = visiblePages.findIndex((p) => p.kind === "description");
  const photoOrDescriptionStep = descriptionStep >= 0 ? descriptionStep : 0;

  const unmet: { key: string; label: string; step: number }[] = [];
  if (!content.hasPhotoOrDescription) {
    unmet.push({
      key: "photoOrDescription",
      label: t("stepReview.missingPhotoOrDescription"),
      step: photoOrDescriptionStep,
    });
  }
  if (!content.hasLocationOrRoute) {
    unmet.push({
      key: "locationOrRoute",
      label: t("stepReview.missingLocationOrRoute"),
      step: locationStep >= 0 ? locationStep : 0,
    });
  }
  if (!content.hasDamageClass) {
    unmet.push({ key: "damage", label: t("stepReview.missingDamage"), step: 0 });
  }
  const canSubmit = content.submittable;

  const exifGps = formState.photo_metadata?.gps ?? null;
  const pickedLat = formState.latitude;
  const pickedLng = formState.longitude;
  const photoGpsDivergenceM =
    exifGps && pickedLat !== null && pickedLng !== null
      ? metersBetween(exifGps, { latitude: pickedLat, longitude: pickedLng })
      : null;
  const showPhotoGpsMismatch =
    photoGpsDivergenceM !== null && photoGpsDivergenceM > PHOTO_GPS_DIVERGENCE_M;

  const ctaLabel = submitting
    ? t("stepReview.saving")
    : online
      ? t("stepReview.submit")
      : t("stepReview.queueReport");

  async function handleSubmit() {
    if (submitting || !canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <StepShell
      stepNumber={stepNumber}
      totalSteps={totalSteps}
      onBack={onBack}
      footer={
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting || !canSubmit}
          aria-disabled={submitting || !canSubmit}
          style={{
            width: "100%",
            height: 52,
            borderRadius: 12,
            border: "none",
            background: !canSubmit ? "var(--c-line, #d1d5db)" : submitting ? "#86efac" : "#16a34a",
            fontSize: 16,
            fontWeight: 700,
            color: !canSubmit ? "var(--c-ink-3, #6b7280)" : "#fff",
            cursor: submitting || !canSubmit ? "default" : "pointer",
          }}
        >
          {ctaLabel}
        </button>
      }
    >
      <div
        style={{
          padding: "16px 16px 0",
          display: "flex",
          flexDirection: "column",
          flex: 1,
        }}
      >
        <h2 style={{ fontSize: 20, fontWeight: 700, color: "var(--c-ink)", margin: "0 0 12px" }}>
          {t("stepReview.title")}
        </h2>

        {crisisName?.trim() && (
          <div
            style={{
              marginBottom: 12,
              padding: "8px 12px",
              borderRadius: 10,
              background: "var(--c-blue-50)",
              border: "1px solid var(--c-line)",
              fontSize: 13,
              color: "var(--c-ink-2)",
            }}
          >
            {t("stepReview.reportingOn", {
              defaultValue: "Reporting on: {{crisis}}",
              crisis: crisisName.trim(),
            })}
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "center",
            marginBottom: 12,
          }}
        >
          <div style={{ position: "relative", display: "inline-flex" }}>
            {photoUrl ? (
              <img
                decoding="async"
                src={photoUrl}
                alt=""
                style={{
                  display: "block",
                  maxWidth: "100%",
                  maxHeight: 180,
                  width: "auto",
                  height: "auto",
                  borderRadius: 10,
                }}
              />
            ) : (
              <span
                style={{
                  color: "var(--c-ink-3)",
                  fontSize: 13,
                  fontStyle: "italic",
                  padding: "8px 0",
                }}
              >
                {t("stepReview.noPhoto")}
              </span>
            )}
            <button
              type="button"
              onClick={() => onEdit(0)}
              aria-label={t("stepReview.editPhotoAria")}
              style={{
                position: photoUrl ? "absolute" : "static",
                top: photoUrl ? 6 : undefined,
                insetInlineEnd: photoUrl ? 6 : undefined,
                marginInlineStart: photoUrl ? 0 : 6,
                // 44×44 minimum touch target (WCAG 2.5.5 / Apple HIG).
                width: 44,
                height: 44,
                border: "none",
                background: photoUrl ? "rgba(0,0,0,0.45)" : "none",
                borderRadius: photoUrl ? 6 : 0,
                padding: 0,
                color: photoUrl ? "#fff" : "var(--c-blue-700, #1d4ed8)",
                cursor: "pointer",
                display: "grid",
                placeItems: "center",
              }}
            >
              <PencilIcon />
            </button>
          </div>
        </div>

        {buildReviewRows(visiblePages, formState, t).map((row) => (
          <Row
            key={row.key}
            label={row.label}
            value={row.value}
            onEdit={() => onEdit(row.sourcePageIndex)}
            editAriaLabel={t("stepReview.editAria", { field: row.label })}
          />
        ))}

        {showPhotoGpsMismatch && photoGpsDivergenceM !== null && (
          <div
            role="note"
            data-testid="photo-gps-mismatch-notice"
            style={{
              marginTop: 12,
              padding: "10px 12px",
              borderRadius: 10,
              background: "var(--c-warn-bg, #fef3c7)",
              border: "1px solid var(--c-warn, #d97706)",
              color: "var(--c-warn, #92400e)",
              fontSize: 13,
              lineHeight: 1.4,
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 2 }}>
              {t("stepReview.photoGpsMismatch.title")}
            </div>
            <div>
              {t("stepReview.photoGpsMismatch.body", {
                meters: Math.round(photoGpsDivergenceM),
              })}
            </div>
          </div>
        )}

        {unmet.length > 0 && (
          <div
            role="alert"
            data-testid="review-missing-requirements"
            style={{
              marginTop: 12,
              padding: "10px 12px",
              borderRadius: 10,
              background: "var(--c-danger-bg, #fee2e2)",
              border: "1px solid var(--c-danger, #dc2626)",
              fontSize: 13,
              lineHeight: 1.4,
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 6, color: "var(--c-danger, #991b1b)" }}>
              {t("stepReview.fixRequired")}
            </div>
            <ul style={{ margin: 0, paddingInlineStart: 18, display: "grid", gap: 4 }}>
              {unmet.map((item) => (
                <li key={item.key} style={{ color: "var(--c-ink)" }}>
                  <button
                    type="button"
                    onClick={() => onEdit(item.step)}
                    style={{
                      border: "none",
                      background: "none",
                      padding: 0,
                      color: "var(--c-blue-700, #1d4ed8)",
                      fontSize: 13,
                      textAlign: "start",
                      cursor: "pointer",
                      textDecoration: "underline",
                    }}
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </StepShell>
  );
}
