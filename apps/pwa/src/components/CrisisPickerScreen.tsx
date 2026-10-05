import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getCrises } from "../api/reports";
import { readCrisesCache } from "../lib/precache";
import type { Crisis } from "../types";
import { StepShell } from "./StepShell";

interface CrisisPickerScreenProps {
  onPick: (crisis: Crisis) => void;
  onBack: () => void;
  bannerMessage?: string;
}

export function CrisisPickerScreen({ onPick, onBack, bannerMessage }: CrisisPickerScreenProps) {
  const { t } = useTranslation();
  const [crises, setCrises] = useState<Crisis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = crises.find((c) => c.id === selectedId) ?? null;

  const load = useCallback(async (isCancelled: () => boolean = () => false) => {
    setLoading(true);
    setError(false);
    try {
      const list = await getCrises();
      if (isCancelled()) return;
      setCrises(list);
    } catch {
      if (isCancelled()) return;
      const cached = readCrisesCache();
      if (cached && cached.length > 0) setCrises(cached);
      else setError(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void load(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [load]);

  function retry() {
    void load();
  }

  return (
    <StepShell
      onBack={onBack}
      footer={
        !loading && !(error && crises.length === 0) ? (
          <>
            <button
              type="button"
              disabled={!selected}
              onClick={() => selected && onPick(selected)}
              style={{
                width: "100%",
                height: 48,
                borderRadius: 12,
                border: "none",
                background: selected ? "var(--c-blue-700)" : "var(--c-line)",
                fontSize: 15,
                fontWeight: 600,
                color: selected ? "#fff" : "var(--c-ink-4)",
                cursor: selected ? "pointer" : "not-allowed",
              }}
            >
              {t("crisisPicker.confirm")}
            </button>
            <div
              style={{
                textAlign: "center",
                fontSize: 11,
                color: "var(--c-ink-4)",
                marginTop: 10,
                lineHeight: 1.45,
              }}
            >
              {t("crisisPicker.footer")}
            </div>
          </>
        ) : undefined
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
          {t("crisisPicker.title")}
        </h2>

        {bannerMessage && (
          <div
            role="alert"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
              padding: "10px 12px",
              background: "var(--c-danger-bg)",
              border: "1px solid var(--c-danger)",
              borderRadius: 12,
              color: "var(--c-danger)",
              fontSize: 12,
              fontWeight: 600,
              marginBottom: 12,
              lineHeight: 1.4,
            }}
          >
            <AlertIcon />
            <span>{bannerMessage}</span>
          </div>
        )}

        {loading ? (
          <LoadingList />
        ) : error && crises.length === 0 ? (
          <ErrorState onRetry={retry} />
        ) : (
          <>
            <div
              style={{
                display: "inline-flex",
                alignSelf: "flex-start",
                alignItems: "center",
                gap: 8,
                background: "var(--c-blue-100)",
                color: "var(--c-blue-700)",
                padding: "6px 12px",
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
                marginBottom: 14,
              }}
            >
              <PulseDot />
              {crises.length === 1
                ? t("crisisPicker.operationActive", { count: crises.length })
                : t("crisisPicker.operationsActive", { count: crises.length })}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {crises.map((c) => {
                const isActive = selectedId === c.id;
                return (
                  <button
                    key={c.id}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setSelectedId(c.id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      padding: "14px",
                      borderRadius: 14,
                      border: isActive
                        ? "2px solid var(--c-blue-700)"
                        : "1.5px solid var(--c-line)",
                      background: isActive ? "var(--c-blue-50)" : "var(--c-card)",
                      textAlign: "start",
                      cursor: "pointer",
                      minHeight: 60,
                      transition: "border-color 0.12s, background 0.12s",
                      fontFamily: "var(--font-ui)",
                    }}
                  >
                    <span
                      style={{
                        width: 22,
                        height: 22,
                        borderRadius: "50%",
                        flexShrink: 0,
                        border: isActive ? "none" : "1.5px solid var(--c-line)",
                        background: isActive ? "var(--c-blue-700)" : "var(--c-card)",
                        display: "grid",
                        placeItems: "center",
                      }}
                    >
                      {isActive && (
                        <svg
                          aria-hidden="true"
                          width="11"
                          height="11"
                          viewBox="0 0 12 12"
                          fill="none"
                        >
                          <path
                            d="M2 6l3 3 5-5"
                            stroke="#fff"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      )}
                    </span>

                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 15 }}>{c.name}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </StepShell>
  );
}

function LoadingList() {
  return (
    <div
      data-testid="crisis-picker-loading"
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
    >
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          aria-hidden="true"
          style={{
            height: 60,
            borderRadius: 14,
            background: "var(--c-line-2)",
            opacity: 0.5,
          }}
        />
      ))}
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <div style={{ padding: "8px 4px 4px" }}>
      <h2 style={{ fontSize: 18, fontWeight: 700, margin: "0 0 6px" }}>
        {t("crisisPicker.errorTitle")}
      </h2>
      <p style={{ fontSize: 13, color: "var(--c-ink-3)", margin: "0 0 14px", lineHeight: 1.45 }}>
        {t("crisisPicker.errorBody")}
      </p>
      <button
        type="button"
        onClick={onRetry}
        style={{
          width: "100%",
          padding: "14px 18px",
          borderRadius: 12,
          border: "none",
          background: "var(--c-blue-700)",
          color: "#fff",
          fontSize: 16,
          fontWeight: 700,
          cursor: "pointer",
          fontFamily: "var(--font-ui)",
        }}
      >
        {t("crisisPicker.retry")}
      </button>
    </div>
  );
}

function PulseDot() {
  return (
    <span
      aria-hidden="true"
      style={{
        width: 7,
        height: 7,
        borderRadius: 999,
        background: "var(--c-danger)",
        display: "inline-block",
      }}
    />
  );
}

function AlertIcon() {
  return (
    <svg
      aria-hidden="true"
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      style={{ flexShrink: 0, marginTop: 1 }}
    >
      <path
        d="M8 1.5L15 14H1L8 1.5z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M8 6v3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="11.5" r="0.7" fill="currentColor" />
    </svg>
  );
}
