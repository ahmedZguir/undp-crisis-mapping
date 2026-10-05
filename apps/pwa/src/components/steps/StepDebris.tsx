import { useTranslation } from "react-i18next";
import debrisNoIcon from "../../assets/debris-no.webp";
import debrisYesIcon from "../../assets/debris-yes.webp";
import type { FormState } from "../../types";
import { StepShell } from "../StepShell";

type DebrisValue = NonNullable<FormState["debris"]>;

interface Props {
  value: DebrisValue | null;
  onChange: (v: DebrisValue) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
  /** When false, the citizen may continue without choosing. Default true. */
  required?: boolean;
}

interface DebrisOption {
  labelKey: string;
  value: DebrisValue;
  /** Shown only when there is no image. */
  emoji?: string;
  image?: string;
  bg: string;
  selectedBorder: string;
}

const OPTIONS: DebrisOption[] = [
  {
    labelKey: "stepDebris.yes",
    value: "yes",
    image: debrisYesIcon,
    bg: "var(--c-warn-bg)",
    selectedBorder: "var(--c-warn)",
  },
  {
    labelKey: "stepDebris.no",
    value: "no",
    image: debrisNoIcon,
    bg: "var(--c-safe-bg)",
    selectedBorder: "var(--c-safe)",
  },
  {
    labelKey: "stepDebris.unknown",
    value: "unknown",
    emoji: "❓",
    bg: "var(--c-line-2)",
    selectedBorder: "var(--c-ink-3)",
  },
];

export function StepDebris({
  value,
  onChange,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
  required = true,
}: Props) {
  const { t } = useTranslation();
  const blocked = required && !value;

  return (
    <StepShell
      stepNumber={stepNumber}
      totalSteps={totalSteps}
      onBack={onBack}
      footer={
        <button
          type="button"
          onClick={onNext}
          disabled={blocked}
          style={{
            width: "100%",
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
          {t("stepDebris.next")}
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
            fontSize: "calc(19px * var(--ui-scale, 1))",
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 12px",
          }}
        >
          {t("stepDebris.title")}
        </h2>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {OPTIONS.map((opt) => {
            const selected = value === opt.value;
            const label = t(opt.labelKey);
            return (
              <button
                key={opt.value}
                type="button"
                aria-label={label}
                aria-pressed={selected}
                onClick={() => onChange(opt.value)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 14,
                  padding: "0 18px",
                  flex: 1,
                  minHeight: "calc(76px * var(--ui-scale, 1))",
                  borderRadius: 14,
                  border: `2px solid ${selected ? opt.selectedBorder : "var(--c-line)"}`,
                  background: selected ? opt.bg : "var(--c-card)",
                  cursor: "pointer",
                  textAlign: "start",
                }}
              >
                {opt.image ? (
                  <img
                    decoding="async"
                    src={opt.image}
                    alt=""
                    aria-hidden="true"
                    style={{ width: 44, height: 44, objectFit: "contain", flexShrink: 0 }}
                  />
                ) : (
                  <span aria-hidden="true" style={{ fontSize: 32 }}>
                    {opt.emoji}
                  </span>
                )}
                <span
                  style={{
                    fontSize: 17,
                    fontWeight: 700,
                    color: "var(--c-ink)",
                  }}
                >
                  {label}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </StepShell>
  );
}
