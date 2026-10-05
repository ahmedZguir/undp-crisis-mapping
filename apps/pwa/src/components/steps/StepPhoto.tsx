import type React from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import completeIcon from "../../assets/damage-complete.webp";
import minimalIcon from "../../assets/damage-minimal.webp";
import partialIcon from "../../assets/damage-partial.webp";
import { useObjectUrl } from "../../hooks/useObjectUrl";
import type { PhotoMetadata } from "../../lib/photoMetadata";
import { camera } from "../../platform/camera";
import type { DamageClass } from "../../types/index";
import { PhotoEditor } from "../PhotoEditor";
import { StepShell } from "../StepShell";

interface StepPhotoProps {
  value: File | null;
  onChange: (file: File) => void;
  onMetadataChange?: (meta: PhotoMetadata | null) => void;
  damageClass: DamageClass | null;
  aiSuggestedClass?: DamageClass | null;
  aiClassifying?: boolean;
  onChangeDamageClass: (v: DamageClass) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
  /** Raw capture, previewed while the compression worker produces `value`. */
  previewFile?: File | null;
}

interface DamageOption {
  labelKey: string;
  value: DamageClass;
  descKey: string;
  bg: string;
  selectedBorder: string;
  iconSrc: string;
}

const OPTIONS: DamageOption[] = [
  {
    labelKey: "stepPhoto.minimal",
    value: "minimal",
    descKey: "stepPhoto.minimalDesc",
    bg: "var(--c-safe-bg)",
    selectedBorder: "var(--c-safe)",
    iconSrc: minimalIcon,
  },
  {
    labelKey: "stepPhoto.partial",
    value: "partial",
    descKey: "stepPhoto.partialDesc",
    bg: "var(--c-warn-bg)",
    selectedBorder: "var(--c-warn)",
    iconSrc: partialIcon,
  },
  {
    labelKey: "stepPhoto.complete",
    value: "complete",
    descKey: "stepPhoto.completeDesc",
    bg: "var(--c-danger-bg)",
    selectedBorder: "var(--c-danger)",
    iconSrc: completeIcon,
  },
];

const OVERLAY_PILL: React.CSSProperties = {
  padding: "6px 12px",
  borderRadius: 999,
  border: "1px solid rgba(255,255,255,0.4)",
  background: "rgba(0,0,0,0.55)",
  fontSize: 12,
  fontWeight: 600,
  color: "#fff",
  cursor: "pointer",
};

function GalleryIcon({ size, strokeWidth }: { size: number; strokeWidth: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect x="2" y="4" width="20" height="16" rx="2" stroke="white" strokeWidth={strokeWidth} />
      <path
        d="M2 15.5l4.5-4.5 3.5 3.5 3-3L22 17"
        stroke="white"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="9.5" r="1.5" fill="white" />
    </svg>
  );
}

export function StepPhoto({
  value,
  onChange,
  onMetadataChange,
  damageClass,
  aiSuggestedClass,
  aiClassifying,
  onChangeDamageClass,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
  previewFile,
}: StepPhotoProps) {
  const { t } = useTranslation();
  const [showValidation, setShowValidation] = useState(false);
  const [editing, setEditing] = useState<File | null>(null);

  const displayBlob = value ?? previewFile ?? null;
  const previewUrl = useObjectUrl(displayBlob);

  async function doCapture() {
    // Web opens the picker synchronously (iOS Safari requires it for `capture`).
    const result = await camera.capturePhoto();
    if (!result) return;
    onMetadataChange?.(result.metadata);
    onChange(result.blob);
  }

  async function doGalleryPick() {
    const result = await camera.pickFromGallery();
    if (!result) return;
    onMetadataChange?.(result.metadata);
    onChange(result.blob);
  }

  function handleNext() {
    if (!damageClass) {
      setShowValidation(true);
      return;
    }
    setShowValidation(false);
    onNext();
  }

  return (
    <StepShell
      stepNumber={stepNumber}
      totalSteps={totalSteps}
      onBack={onBack}
      footer={
        <div style={{ display: "flex", gap: 10 }}>
          <button
            type="button"
            onClick={handleNext}
            style={{
              flex: 1,
              height: 48,
              borderRadius: 12,
              border: "none",
              background: "var(--c-blue-700)",
              fontSize: 15,
              fontWeight: 600,
              color: "#fff",
              cursor: "pointer",
            }}
          >
            {t("stepPhoto.next")}
          </button>
        </div>
      }
    >
      <div
        style={{
          padding: "calc(20px * var(--ui-scale, 1)) 16px 0",
          display: "flex",
          flexDirection: "column",
          flex: 1,
        }}
      >
        <h2
          style={{
            fontSize: "calc(22px * var(--ui-scale, 1))",
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 16px",
          }}
        >
          {t("stepPhoto.title")}
        </h2>

        <div
          style={{
            background: "#1a1a2a",
            borderRadius: 14,
            height: "calc(280px * var(--ui-scale, 1))",
            flexShrink: 0,
            position: "relative",
            overflow: "hidden",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexDirection: "column",
            marginBottom: 16,
          }}
        >
          {previewUrl ? (
            <>
              <img
                decoding="async"
                src={previewUrl}
                alt={t("stepPhoto.preview")}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                  borderRadius: 14,
                }}
              />
              <div
                style={{
                  position: "absolute",
                  top: 10,
                  right: 10,
                  display: "flex",
                  gap: 6,
                  zIndex: 2,
                }}
              >
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (value) setEditing(value);
                  }}
                  style={OVERLAY_PILL}
                >
                  {t("stepPhoto.cropZoom")}
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    // Must stay synchronous in this gesture: iOS Safari requires it for `capture`.
                    void doCapture();
                  }}
                  style={OVERLAY_PILL}
                >
                  {t("stepPhoto.retake")}
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void doGalleryPick();
                  }}
                  style={{
                    ...OVERLAY_PILL,
                    padding: "6px 10px",
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                  }}
                >
                  <GalleryIcon size={14} strokeWidth={1.8} />
                  {t("stepPhoto.gallery")}
                </button>
              </div>
            </>
          ) : (
            <>
              <svg
                width="48"
                height="48"
                viewBox="0 0 48 48"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
                style={{ marginBottom: 12 }}
                aria-hidden="true"
              >
                <path
                  d="M16 14L18.5 9H29.5L32 14H40C41.1 14 42 14.9 42 16V36C42 37.1 41.1 38 40 38H8C6.9 38 6 37.1 6 36V16C6 14.9 6.9 14 8 14H16Z"
                  stroke="white"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <circle cx="24" cy="26" r="6" stroke="white" strokeWidth="2" />
              </svg>
              <p
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: "rgba(255,255,255,0.6)",
                  margin: "0 0 4px",
                }}
              >
                {t("stepPhoto.tapToCapture")}
              </p>
              <p
                style={{
                  fontSize: 12,
                  color: "rgba(255,255,255,0.4)",
                  margin: 0,
                }}
              >
                {t("stepPhoto.subtitle")}
              </p>
              <button
                type="button"
                onClick={() => void doCapture()}
                aria-label={t("stepPhoto.uploadAria")}
                style={{
                  position: "absolute",
                  inset: 0,
                  opacity: 0,
                  cursor: "pointer",
                  border: "none",
                  background: "transparent",
                }}
              />
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  void doGalleryPick();
                }}
                aria-label={t("stepPhoto.gallery")}
                style={{
                  position: "absolute",
                  bottom: 14,
                  right: 14,
                  zIndex: 2,
                  width: 56,
                  height: 56,
                  borderRadius: 14,
                  border: "1px solid rgba(255,255,255,0.35)",
                  background: "rgba(0,0,0,0.5)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 4,
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                <GalleryIcon size={26} strokeWidth={1.5} />
                <span
                  style={{
                    fontSize: 9,
                    fontWeight: 600,
                    color: "rgba(255,255,255,0.85)",
                    letterSpacing: 0.2,
                    lineHeight: 1,
                  }}
                >
                  {t("stepPhoto.gallery")}
                </span>
              </button>
            </>
          )}
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            margin: "0 0 10px",
            flexWrap: "wrap",
          }}
        >
          <h3
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: "var(--c-ink)",
              margin: 0,
            }}
          >
            {t("stepPhoto.severity")}
          </h3>
          {aiClassifying && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                fontWeight: 600,
                color: "var(--c-blue-700)",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 13,
                  height: 13,
                  borderRadius: 999,
                  border: "2px solid var(--c-blue-50, rgba(37,99,235,0.2))",
                  borderTopColor: "var(--c-blue-700)",
                  animation: "spin 0.7s linear infinite",
                  flexShrink: 0,
                }}
              />
              {t("stepPhoto.aiAnalyzing")}
            </span>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {OPTIONS.map((opt) => {
            const selected = damageClass === opt.value;
            const aiSuggested = aiSuggestedClass === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  onChangeDamageClass(opt.value);
                  setShowValidation(false);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 12px",
                  borderRadius: 12,
                  border: `2px solid ${selected ? opt.selectedBorder : "var(--c-line)"}`,
                  background: selected ? opt.bg : "var(--c-card)",
                  cursor: "pointer",
                  textAlign: "start",
                  width: "100%",
                }}
              >
                <img
                  decoding="async"
                  src={opt.iconSrc}
                  alt=""
                  aria-hidden="true"
                  width={40}
                  height={40}
                  style={{ objectFit: "contain", flexShrink: 0 }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      flexWrap: "wrap",
                      fontSize: 14,
                      fontWeight: 700,
                      color: "var(--c-ink)",
                    }}
                  >
                    {t(opt.labelKey)}
                    {aiSuggested && (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: 0.2,
                          color: "var(--c-blue-700)",
                          background: "var(--c-blue-50, rgba(37,99,235,0.1))",
                          border: "1px solid var(--c-blue-700)",
                          borderRadius: 999,
                          padding: "1px 7px",
                          textTransform: "uppercase",
                        }}
                      >
                        {t("stepPhoto.aiSuggested")}
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--c-ink-3)",
                      marginTop: 2,
                      lineHeight: 1.35,
                    }}
                  >
                    {t(opt.descKey)}
                  </div>
                </div>
                {selected && (
                  <svg
                    width="20"
                    height="20"
                    viewBox="0 0 18 18"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                    aria-hidden="true"
                    style={{ flexShrink: 0 }}
                  >
                    <path
                      d="M4 9L7.5 12.5L14 6"
                      stroke="var(--c-blue-700)"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
              </button>
            );
          })}
        </div>

        {showValidation && (
          <p role="alert" style={{ color: "var(--c-danger)", fontSize: 13, margin: "10px 0 0" }}>
            {t("stepPhoto.validation")}
          </p>
        )}
      </div>
      {editing && (
        <PhotoEditor
          file={editing}
          onCancel={() => setEditing(null)}
          onApply={(edited) => {
            setEditing(null);
            onChange(edited);
          }}
        />
      )}
    </StepShell>
  );
}
