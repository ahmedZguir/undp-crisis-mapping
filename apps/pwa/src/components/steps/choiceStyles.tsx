// Selection styling shared by the infra-type and crisis-nature choice steps.
import type { CSSProperties } from "react";

export const SELECTED_COLOR = "var(--c-blue-800)";
export const SELECTED_BG = "var(--c-blue-100)";
export const SELECTED_BORDER = "var(--c-blue-600)";

export const OTHER_INPUT_STYLE: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "12px 14px",
  borderRadius: 10,
  border: "1.5px solid var(--c-line)",
  fontSize: 16,
  fontFamily: "var(--font-ui)",
  color: "var(--c-ink)",
  background: "var(--c-card)",
  outline: "none",
};

export function OtherDotsIcon({ boxSize }: { boxSize: number }) {
  return (
    <div
      aria-hidden="true"
      style={{
        width: boxSize,
        height: boxSize,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
      }}
    >
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--c-ink)",
          }}
        />
      ))}
    </div>
  );
}
