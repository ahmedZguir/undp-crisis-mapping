import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { CrisisNatureType } from "../../types";
import { StepShell } from "../StepShell";
import { CRISIS_NATURE_OTHER, CRISIS_NATURE_TYPES } from "./catalogues";
import {
  OTHER_INPUT_STYLE,
  OtherDotsIcon,
  SELECTED_BG,
  SELECTED_BORDER,
  SELECTED_COLOR,
} from "./choiceStyles";

const ICON_SIZE = 42;

interface Props {
  crisisNature: string | null;
  crisisNatureOther: string;
  onChangeCrisisNatureType: (t: CrisisNatureType | null) => void;
  onChangeCrisisNature: (v: string | null) => void;
  onChangeCrisisNatureOther: (v: string) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
  nextLabel?: string;
  /** When false, the citizen may continue without choosing. Default true. */
  required?: boolean;
}

export function StepCrisisNature({
  crisisNature,
  crisisNatureOther,
  onChangeCrisisNatureType,
  onChangeCrisisNature,
  onChangeCrisisNatureOther,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
  nextLabel,
  required = true,
}: Props) {
  const { t } = useTranslation();
  const isOtherSelected = crisisNature === CRISIS_NATURE_OTHER;
  const [showValidation, setShowValidation] = useState(false);
  const resolvedNextLabel = nextLabel ?? t("stepInfraDetails.next");

  function handleNext() {
    if (required && !crisisNature) {
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
        <button
          type="button"
          onClick={handleNext}
          style={{
            width: "100%",
            height: 52,
            borderRadius: 14,
            border: "none",
            background: "var(--c-blue-700)",
            fontSize: 16,
            fontWeight: 700,
            color: "#fff",
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
          }}
        >
          {resolvedNextLabel}
        </button>
      }
    >
      <div
        style={{
          padding: "calc(14px * var(--ui-scale, 1)) 16px 0",
          display: "flex",
          flexDirection: "column",
          flex: 1,
        }}
      >
        <h2
          style={{
            fontSize: "calc(21px * var(--ui-scale, 1))",
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 14px",
          }}
        >
          {t("stepInfraDetails.title")}
        </h2>

        <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 8 }}>
          {CRISIS_NATURE_TYPES.map((opt) => (
            <div
              key={opt.value}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "stretch",
                gap: 6,
              }}
            >
              <div
                style={{
                  alignSelf: "flex-start",
                  textAlign: "start",
                  fontSize: 13,
                  fontWeight: 700,
                  color: opt.headerColor,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  lineHeight: 1.2,
                }}
              >
                {t(opt.labelKey)}
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  gap: 8,
                  minWidth: 0,
                }}
              >
                {opt.subs.map((sub) => {
                  const subSelected = crisisNature === sub.label;
                  return (
                    <button
                      key={sub.label}
                      type="button"
                      aria-pressed={subSelected}
                      onClick={() => {
                        if (subSelected) {
                          onChangeCrisisNature(null);
                          onChangeCrisisNatureType(null);
                        } else {
                          onChangeCrisisNatureType(opt.value);
                          onChangeCrisisNature(sub.label);
                        }
                        if (showValidation) setShowValidation(false);
                      }}
                      style={{
                        width: "100%",
                        minHeight: "calc(56px * var(--ui-scale, 1))",
                        display: "flex",
                        flexDirection: "row",
                        alignItems: "center",
                        justifyContent: "flex-start",
                        gap: 12,
                        padding: "10px 12px",
                        borderRadius: 12,
                        border: `2px solid ${subSelected ? SELECTED_BORDER : "var(--c-line)"}`,
                        background: subSelected ? SELECTED_BG : "var(--c-card)",
                        cursor: "pointer",
                        transition: "border-color 0.12s, background 0.12s",
                        fontFamily: "var(--font-ui)",
                        textAlign: "start",
                      }}
                    >
                      <img
                        decoding="async"
                        src={sub.icon}
                        alt=""
                        aria-hidden="true"
                        width={ICON_SIZE}
                        height={ICON_SIZE}
                        style={{ objectFit: "contain", flexShrink: 0 }}
                      />
                      <span
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          minWidth: 0,
                          flex: 1,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 14,
                            fontWeight: subSelected ? 700 : 600,
                            color: subSelected ? SELECTED_COLOR : "var(--c-ink)",
                            letterSpacing: "-0.01em",
                            lineHeight: 1.2,
                            wordBreak: "break-word",
                          }}
                        >
                          {t(sub.labelKey)}
                        </span>
                        {sub.sublabelKey && (
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 500,
                              color: subSelected ? SELECTED_COLOR : "var(--c-ink-3)",
                              lineHeight: 1.2,
                              marginTop: 2,
                              wordBreak: "break-word",
                            }}
                          >
                            {t(sub.sublabelKey)}
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          <hr
            style={{
              border: "none",
              borderTop: "1px solid var(--c-line)",
              margin: "2px 0",
            }}
          />

          <div>
            <button
              type="button"
              aria-pressed={isOtherSelected}
              onClick={() => {
                onChangeCrisisNatureType(null);
                onChangeCrisisNature(isOtherSelected ? null : CRISIS_NATURE_OTHER);
                if (showValidation) setShowValidation(false);
              }}
              style={{
                width: "100%",
                minHeight: "calc(56px * var(--ui-scale, 1))",
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "flex-start",
                gap: 12,
                padding: "8px 14px",
                borderRadius: 12,
                border: `2px solid ${isOtherSelected ? SELECTED_BORDER : "var(--c-line)"}`,
                background: isOtherSelected ? SELECTED_BG : "var(--c-card)",
                cursor: "pointer",
                transition: "border-color 0.12s, background 0.12s",
                fontFamily: "var(--font-ui)",
                textAlign: "start",
              }}
            >
              <OtherDotsIcon boxSize={36} />
              <span
                style={{
                  fontSize: 15,
                  fontWeight: isOtherSelected ? 700 : 600,
                  color: isOtherSelected ? SELECTED_COLOR : "var(--c-ink)",
                  letterSpacing: "-0.01em",
                }}
              >
                {t("stepInfraDetails.other")}
              </span>
            </button>

            {isOtherSelected && (
              <div style={{ marginTop: 8 }}>
                <div
                  style={{
                    marginBottom: 4,
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--c-ink-2)",
                  }}
                >
                  {t("stepInfraDetails.describe")}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--c-ink-3)",
                    lineHeight: 1.4,
                    marginBottom: 6,
                  }}
                >
                  {t("stepInfraDetails.example")}
                </div>
                <input
                  type="text"
                  aria-label={t("stepInfraDetails.otherAria")}
                  value={crisisNatureOther}
                  onChange={(e) => {
                    onChangeCrisisNatureOther(e.target.value);
                    if (showValidation) setShowValidation(false);
                  }}
                  style={OTHER_INPUT_STYLE}
                />
              </div>
            )}
          </div>
        </div>

        {showValidation && (
          <p role="alert" style={{ color: "var(--c-danger)", fontSize: 16, margin: "12px 0 0" }}>
            {t("stepInfraDetails.validation")}
          </p>
        )}
      </div>
    </StepShell>
  );
}
