// Question-type picker for "+ Add question", compact enough for the phone preview.
import type { FormQuestionType } from "../../../types";

interface Props {
  onPick: (qType: FormQuestionType) => void;
  onCancel: () => void;
}

const OPTIONS: Array<{ type: FormQuestionType; label: string }> = [
  { type: "single_select", label: "Single select" },
  { type: "multi_select", label: "Multi select" },
  { type: "free_text", label: "Free text" },
];

export function AddQuestionMenu({ onPick, onCancel }: Props) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
        padding: 5,
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 8,
          color: "var(--c-ink-3)",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: 0.4,
          paddingInline: 2,
        }}
      >
        <span>New question</span>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel"
          title="Cancel"
          style={{
            width: 14,
            height: 14,
            display: "grid",
            placeItems: "center",
            padding: 0,
            background: "transparent",
            border: "none",
            color: "var(--c-ink-3)",
            cursor: "pointer",
            borderRadius: 4,
          }}
        >
          <svg
            width={9}
            height={9}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <title>Cancel</title>
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      {OPTIONS.map((o) => (
        <button
          key={o.type}
          type="button"
          className="fb-option-tile"
          onClick={() => onPick(o.type)}
          style={{
            textAlign: "start",
            padding: "4px 7px",
            fontSize: 10,
            fontWeight: 600,
            color: "var(--c-ink-2)",
            border: "1px solid var(--c-line)",
            borderRadius: 6,
            background: "var(--c-card)",
            cursor: "pointer",
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
