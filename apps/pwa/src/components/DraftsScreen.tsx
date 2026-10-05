import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useIsRtl } from "../hooks/useIsRtl";
import type { DraftRow } from "../lib/db";
import { DraftCard } from "./DraftCard";
import { ConfirmDialog } from "./ui/ConfirmDialog";

interface DraftsScreenProps {
  drafts: DraftRow[];
  onContinue: (id: string) => void;
  onDiscard: (id: string) => void;
  onBack: () => void;
}

export function DraftsScreen({ drafts, onContinue, onDiscard, onBack }: DraftsScreenProps) {
  const { t } = useTranslation();
  const isRtl = useIsRtl();
  const [pendingDiscard, setPendingDiscard] = useState<string | null>(null);
  return (
    <div
      style={{
        minHeight: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "var(--c-surface)",
      }}
    >
      <div
        style={{
          padding:
            "max(env(safe-area-inset-top, 18px), 18px) calc(22px + env(safe-area-inset-right)) 14px calc(22px + env(safe-area-inset-left))",
          display: "flex",
          alignItems: "center",
          gap: 10,
          borderBottom: "1px solid var(--c-line-2)",
          background: "var(--c-card)",
        }}
      >
        <button
          type="button"
          onClick={onBack}
          aria-label={t("nav.back", { defaultValue: "Back" })}
          style={{
            width: 36,
            height: 36,
            border: 0,
            background: "transparent",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 0,
          }}
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 18 18"
            fill="none"
            aria-hidden="true"
            style={isRtl ? { transform: "scaleX(-1)" } : undefined}
          >
            <path
              d="M11 4L6 9l5 5"
              stroke="var(--c-ink)"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <div style={{ fontSize: 16, fontWeight: 800 }}>
          {t("drafts.title", { defaultValue: "Drafts" })}
        </div>
      </div>

      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        {drafts.length === 0 ? (
          <div style={{ fontSize: 13, color: "var(--c-ink-3)" }}>
            {t("drafts.empty", { defaultValue: "No drafts yet." })}
          </div>
        ) : (
          drafts.map((d) => (
            <DraftCard
              key={d.id}
              draft={d}
              onContinue={() => onContinue(d.id)}
              onDiscard={() => setPendingDiscard(d.id)}
            />
          ))
        )}
      </div>

      {pendingDiscard && (
        <ConfirmDialog
          titleId="discard-draft-title"
          title={t("draft.discardTitle")}
          body={t("draft.discardBody")}
          cancelLabel={t("draft.discardCancel")}
          confirmLabel={t("draft.discardConfirm")}
          onCancel={() => setPendingDiscard(null)}
          onConfirm={() => {
            onDiscard(pendingDiscard);
            setPendingDiscard(null);
          }}
        />
      )}
    </div>
  );
}
