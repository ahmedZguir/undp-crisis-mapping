// Optional module page, disabled by default in the crisis form schema.
import { useTranslation } from "react-i18next";
import { StepShell } from "../StepShell";

const MAX_SELECTIONS = 3;

interface Props {
  value: string[];
  onChange: (v: string[]) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber?: number;
  totalSteps?: number;
}

// Stored values are canonical English labels (see SingleChoiceStep).
const OPTIONS: { value: string; labelKey: string }[] = [
  { value: "Food", labelKey: "stepPressingNeeds.food" },
  { value: "Water", labelKey: "stepPressingNeeds.water" },
  { value: "Shelter", labelKey: "stepPressingNeeds.shelter" },
  { value: "Medical care", labelKey: "stepPressingNeeds.medicalCare" },
  { value: "Search and rescue", labelKey: "stepPressingNeeds.searchRescue" },
  { value: "Psychosocial support", labelKey: "stepPressingNeeds.psychosocialSupport" },
  { value: "Cash assistance", labelKey: "stepPressingNeeds.cashAssistance" },
  { value: "Other", labelKey: "stepPressingNeeds.other" },
];

export function StepPressingNeeds({
  value,
  onChange,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
}: Props) {
  const { t } = useTranslation();

  function handleSelect(option: string) {
    if (value.includes(option)) {
      onChange(value.filter((v) => v !== option));
    } else if (value.length < MAX_SELECTIONS) {
      onChange([...value, option]);
    } else {
      onChange([...value.slice(1), option]);
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
          onClick={onNext}
          style={{
            width: "100%",
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
          {t("stepPressingNeeds.next")}
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
            margin: "0 0 6px",
          }}
        >
          {t("stepPressingNeeds.title")}
        </h2>
        <p style={{ fontSize: 13, color: "var(--c-ink-3)", margin: "0 0 16px" }}>
          {t("stepPressingNeeds.selectUpTo3")}
        </p>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
          }}
        >
          {OPTIONS.map((opt) => {
            const selected = value.includes(opt.value);
            const label = t(opt.labelKey);
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={selected}
                onClick={() => handleSelect(opt.value)}
                style={{
                  padding: "10px 16px",
                  borderRadius: 999,
                  border: `1.5px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
                  background: selected ? "var(--c-blue-50)" : "var(--c-card)",
                  fontSize: 14,
                  fontWeight: selected ? 600 : 400,
                  color: selected ? "var(--c-blue-700)" : "var(--c-ink-2)",
                  cursor: "pointer",
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
    </StepShell>
  );
}
