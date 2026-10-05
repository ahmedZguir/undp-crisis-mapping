import type { CSSProperties } from "react";

// Eagerly loaded so Suspense fallbacks can use it before the map chunk arrives.
export function MapLoadingIndicator({ label, style }: { label: string; style: CSSProperties }) {
  return (
    <output
      aria-label={label}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        ...style,
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: 36,
          height: 36,
          borderRadius: "50%",
          border: "3px solid var(--c-line)",
          borderTopColor: "var(--c-blue-700)",
          animation: "spin 0.75s linear infinite",
        }}
      />
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--c-ink-2)" }}>{label}</span>
    </output>
  );
}
