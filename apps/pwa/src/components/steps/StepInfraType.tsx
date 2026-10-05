import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { InfraType } from "../../types/index";
import { StepShell } from "../StepShell";
import { INFRA_TYPE_OPTIONS } from "./catalogues";
import {
  OTHER_INPUT_STYLE,
  OtherDotsIcon,
  SELECTED_BG,
  SELECTED_BORDER,
  SELECTED_COLOR,
} from "./choiceStyles";

interface StepInfraTypeProps {
  value: InfraType[];
  otherValue: string;
  onOtherChange: (v: string) => void;
  onChange: (v: InfraType[]) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
  /** When false, the citizen may continue without choosing. Default true. */
  required?: boolean;
}

export function StepInfraType({
  value,
  otherValue,
  onOtherChange,
  onChange,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
  required = true,
}: StepInfraTypeProps) {
  const { t } = useTranslation();
  const [showValidation, setShowValidation] = useState(false);

  const isOtherSelected = value.includes("other");
  const isValid = !required || value.length > 0;

  const handleNext = () => {
    if (!isValid) {
      setShowValidation(true);
      return;
    }
    setShowValidation(false);
    onNext();
  };

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
            borderRadius: 12,
            border: "none",
            background: "var(--c-blue-700)",
            fontSize: 15,
            fontWeight: 700,
            color: "#fff",
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
          }}
        >
          {t("stepInfraType.next")}
        </button>
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
            fontSize: "calc(20px * var(--ui-scale, 1))",
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 12px",
          }}
        >
          {t("stepInfraType.title")}
        </h2>

        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {INFRA_TYPE_OPTIONS.map((opt) => {
            const selected = value.includes(opt.value);
            return (
              <div key={opt.value}>
                <button
                  type="button"
                  aria-pressed={selected}
                  onClick={() => {
                    const next = selected
                      ? value.filter((v) => v !== opt.value)
                      : [...value, opt.value];
                    onChange(next);
                    setShowValidation(false);
                  }}
                  style={{
                    width: "100%",
                    display: "flex",
                    flexDirection: "row",
                    alignItems: "center",
                    gap: 12,
                    padding: opt.value === "other" ? "3px 14px" : "5px 14px",
                    borderRadius: 12,
                    border: `2px solid ${selected ? SELECTED_BORDER : "var(--c-line)"}`,
                    background: selected ? SELECTED_BG : "var(--c-card)",
                    cursor: "pointer",
                    transition: "border-color 0.12s, background 0.12s",
                    fontFamily: "var(--font-ui)",
                    textAlign: "start",
                  }}
                >
                  {opt.icon ? (
                    <img
                      decoding="async"
                      src={opt.icon}
                      alt=""
                      aria-hidden="true"
                      style={{
                        objectFit: "contain",
                        flexShrink: 0,
                        width: "calc(48px * var(--ui-scale, 1))",
                        height: "calc(48px * var(--ui-scale, 1))",
                      }}
                    />
                  ) : (
                    <OtherDotsIcon boxSize={40} />
                  )}
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span
                      style={{
                        fontSize: 15.5,
                        fontWeight: selected ? 700 : 600,
                        color: selected ? SELECTED_COLOR : "var(--c-ink)",
                        letterSpacing: "-0.01em",
                        lineHeight: 1.2,
                      }}
                    >
                      {t(opt.labelKey)}
                    </span>
                    {opt.examplesKey && (
                      <span
                        style={{
                          fontSize: 12.5,
                          color: selected ? SELECTED_COLOR : "var(--c-ink-3)",
                          opacity: selected ? 0.8 : 1,
                          lineHeight: 1.3,
                        }}
                      >
                        {t(opt.examplesKey)}
                      </span>
                    )}
                  </div>
                </button>

                {isOtherSelected && opt.value === "other" && (
                  <input
                    type="text"
                    aria-label={t("stepInfraType.otherAria")}
                    value={otherValue}
                    onChange={(e) => {
                      onOtherChange(e.target.value);
                      setShowValidation(false);
                    }}
                    placeholder={t("stepInfraType.otherPlaceholder")}
                    style={{ ...OTHER_INPUT_STYLE, marginTop: 8 }}
                  />
                )}
              </div>
            );
          })}
        </div>

        {showValidation && (
          <p role="alert" style={{ color: "var(--c-danger)", fontSize: 13, marginTop: 10 }}>
            {t("stepInfraType.validation")}
          </p>
        )}
      </div>
    </StepShell>
  );
}
