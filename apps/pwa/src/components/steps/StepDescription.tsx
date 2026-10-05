import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useObjectUrl } from "../../hooks/useObjectUrl";
import { StepShell } from "../StepShell";
import { VoiceNoteRecorder } from "../VoiceNoteRecorder";

const MAX_DESCRIPTION_LENGTH = 500;

interface StepDescriptionProps {
  photo: File | null;
  onRetake: () => void;
  description: string;
  onChangeDescription: (v: string) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
}

export function StepDescription({
  photo,
  onRetake,
  description,
  onChangeDescription,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
}: StepDescriptionProps) {
  const { t } = useTranslation();
  const previewUrl = useObjectUrl(photo);
  const [showValidation, setShowValidation] = useState(false);

  // Minimum-content rule (photo OR description): with no photo the description is required here.
  const descriptionRequired = !photo;
  const blocked = descriptionRequired && description.trim() === "";

  function handleNext() {
    if (blocked) {
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
            disabled={blocked}
            style={{
              flex: 1,
              height: 48,
              borderRadius: 12,
              border: "none",
              background: blocked ? "var(--c-line)" : "var(--c-blue-700)",
              fontSize: 15,
              fontWeight: 600,
              color: blocked ? "var(--c-ink-4)" : "#fff",
              cursor: blocked ? "not-allowed" : "pointer",
            }}
          >
            {t("stepDescription.next")}
          </button>
        </div>
      }
    >
      <div
        style={{
          padding: "20px 16px 0",
          display: "flex",
          flexDirection: "column",
          flex: 1,
        }}
      >
        <div
          style={{
            position: "relative",
            background: "#1a1a2a",
            borderRadius: 14,
            height: 200,
            flexShrink: 0,
            overflow: "hidden",
            marginBottom: 16,
          }}
        >
          {previewUrl ? (
            <img
              decoding="async"
              src={previewUrl}
              alt={t("stepDescription.altCaptured")}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "contain",
              }}
            />
          ) : (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: "100%",
                height: "100%",
                color: "rgba(255,255,255,0.6)",
                fontSize: 13,
              }}
            >
              {t("stepDescription.noPhoto")}
            </div>
          )}
          <button
            type="button"
            onClick={onRetake}
            style={{
              position: "absolute",
              top: 10,
              right: 10,
              padding: "5px 12px",
              borderRadius: 999,
              border: "1px solid rgba(255,255,255,0.4)",
              background: "rgba(0,0,0,0.55)",
              fontSize: 12,
              fontWeight: 600,
              color: "#fff",
              cursor: "pointer",
            }}
          >
            {t("stepDescription.retake")}
          </button>
        </div>

        <h2
          style={{
            fontSize: 20,
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 14px",
          }}
        >
          {t("stepDescription.title")}
        </h2>

        <div style={{ marginBottom: 16 }}>
          <label
            htmlFor="report-description"
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 700,
              color: "var(--c-ink-2)",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              marginBottom: 4,
            }}
          >
            {t("stepDescription.label")}
            {descriptionRequired ? <span style={{ color: "var(--c-danger)" }}> *</span> : null}
          </label>
          <div style={{ position: "relative" }}>
            <textarea
              id="report-description"
              aria-label={t("stepDescription.aria")}
              value={description}
              rows={4}
              maxLength={MAX_DESCRIPTION_LENGTH}
              placeholder={t("stepDescription.example")}
              onChange={(e) => {
                onChangeDescription(e.target.value.slice(0, MAX_DESCRIPTION_LENGTH));
                setShowValidation(false);
              }}
              style={{
                width: "100%",
                padding: "12px 14px calc(44px * var(--ui-scale, 1))",
                borderRadius: 12,
                border: "1.5px solid var(--c-line)",
                fontSize: 14,
                fontFamily: "var(--font-ui)",
                color: "var(--c-ink)",
                background: "var(--c-card)",
                outline: "none",
                resize: "vertical",
                lineHeight: 1.5,
                boxSizing: "border-box",
              }}
            />
            <div
              style={{ position: "absolute", right: 10, bottom: 10, maxWidth: "calc(100% - 20px)" }}
            >
              <VoiceNoteRecorder
                value={description}
                onChange={(v) => {
                  onChangeDescription(v);
                  setShowValidation(false);
                }}
                maxLength={MAX_DESCRIPTION_LENGTH}
              />
            </div>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
            <span
              aria-label={t("stepDescription.charCount")}
              style={{ fontSize: 11, color: "var(--c-ink-4)" }}
            >
              {description.length}/{MAX_DESCRIPTION_LENGTH}
            </span>
          </div>

          {showValidation && (
            <p role="alert" style={{ color: "var(--c-danger)", fontSize: 13, marginTop: 8 }}>
              {t("stepDescription.requiredWhenNoPhoto")}
            </p>
          )}
        </div>
      </div>
    </StepShell>
  );
}
