// Single-choice list shared by the optional electricity / health-services pages.
import { useTranslation } from "react-i18next";
import { StepShell } from "../StepShell";

// `value` is the canonical English label stored for coordinators (same across
// PWA / WhatsApp / SMS); `labelKey` is the translated citizen-facing label.
export interface SingleChoiceOption {
  value: string;
  labelKey: string;
}

export interface SingleChoiceStepNavProps {
  value: string | null;
  onChange: (v: string) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
}

interface SingleChoiceStepProps extends SingleChoiceStepNavProps {
  /** i18n namespace holding `title` and `next`, e.g. "stepElectricity". */
  i18nPrefix: string;
  options: SingleChoiceOption[];
}

export function SingleChoiceStep({
  i18nPrefix,
  options,
  value,
  onChange,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
}: SingleChoiceStepProps) {
  const { t } = useTranslation();

  return (
    <StepShell
      stepNumber={stepNumber}
      totalSteps={totalSteps}
      onBack={onBack}
      footer={
        <button
          type="button"
          onClick={onNext}
          disabled={!value}
          style={{
            width: "100%",
            height: 48,
            borderRadius: 12,
            border: "none",
            background: !value ? "var(--c-line)" : "var(--c-blue-700)",
            fontSize: 15,
            fontWeight: 600,
            color: !value ? "var(--c-ink-4)" : "#fff",
            cursor: !value ? "not-allowed" : "pointer",
          }}
        >
          {t(`${i18nPrefix}.next`)}
        </button>
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
        <h2
          style={{
            fontSize: 20,
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 16px",
          }}
        >
          {t(`${i18nPrefix}.title`)}
        </h2>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {options.map((opt) => {
            const selected = value === opt.value;
            const label = t(opt.labelKey);
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={selected}
                onClick={() => onChange(opt.value)}
                style={{
                  padding: "14px 16px",
                  borderRadius: 12,
                  border: `1.5px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
                  background: selected ? "var(--c-blue-50)" : "var(--c-card)",
                  fontSize: 14,
                  fontWeight: selected ? 600 : 400,
                  color: selected ? "var(--c-blue-700)" : "var(--c-ink-2)",
                  cursor: "pointer",
                  textAlign: "start",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <span>{label}</span>
                {selected && (
                  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
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
      </div>
    </StepShell>
  );
}
