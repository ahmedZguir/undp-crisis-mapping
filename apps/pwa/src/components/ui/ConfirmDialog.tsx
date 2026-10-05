import type { CSSProperties, ReactNode } from "react";

export const DIALOG_BACKDROP_STYLE: CSSProperties = {
  position: "fixed",
  inset: 0,
  border: 0,
  margin: 0,
  maxWidth: "100vw",
  maxHeight: "100vh",
  width: "100vw",
  height: "100vh",
  background: "rgba(8, 16, 32, 0.55)",
  display: "grid",
  placeItems: "center",
  padding: 20,
  zIndex: 2000,
};

interface ConfirmDialogProps {
  titleId: string;
  title: ReactNode;
  body: ReactNode;
  cancelLabel: ReactNode;
  confirmLabel: ReactNode;
  /** While true both buttons are disabled (e.g. a delete in flight). */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({
  titleId,
  title,
  body,
  cancelLabel,
  confirmLabel,
  busy,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const cursor = busy ? "not-allowed" : "pointer";
  return (
    <dialog open aria-modal="true" aria-labelledby={titleId} style={DIALOG_BACKDROP_STYLE}>
      <div
        style={{
          maxWidth: 380,
          width: "100%",
          background: "var(--c-card)",
          borderRadius: 16,
          padding: "20px 22px",
          boxShadow: "var(--c-shadow-3)",
          fontFamily: "var(--font-ui)",
        }}
      >
        <h2
          id={titleId}
          style={{ margin: 0, fontSize: 18, fontWeight: 800, color: "var(--c-ink)" }}
        >
          {title}
        </h2>
        <p
          style={{
            marginTop: 10,
            marginBottom: 18,
            fontSize: 13,
            lineHeight: 1.5,
            color: "var(--c-ink-2)",
          }}
        >
          {body}
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              border: "1px solid var(--c-line)",
              background: "var(--c-card)",
              color: "var(--c-ink-2)",
              fontSize: 13,
              fontWeight: 600,
              cursor,
            }}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              border: 0,
              background: "var(--c-danger)",
              color: "#fff",
              fontSize: 13,
              fontWeight: 700,
              cursor,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
