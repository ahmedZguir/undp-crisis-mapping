import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useIsRtl } from "../hooks/useIsRtl";
import { LANGUAGES, LANG_PICKED_KEY, type LangCode, langStore } from "../lib/langGate";
import { ArrowRightIcon } from "./icons";

interface LangPickerScreenProps {
  onComplete: () => void;
  // Only set when opened from Settings; the first-run flow has nowhere to go back to.
  onBack?: () => void;
}

export function LangPickerScreen({ onComplete, onBack }: LangPickerScreenProps) {
  const { t, i18n } = useTranslation();
  const isRtl = useIsRtl();
  const [selected, setSelected] = useState<LangCode | null>(null);

  function handleContinue() {
    const lang = selected ?? "en";
    void i18n.changeLanguage(lang);
    langStore().setItem(LANG_PICKED_KEY, lang);
    onComplete();
  }

  return (
    <div
      style={{
        height: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "linear-gradient(180deg, var(--c-blue-700) 0%, var(--c-blue-600) 100%)",
        fontFamily: "var(--font-ui)",
        color: "#fff",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <svg
        aria-hidden="true"
        style={{ position: "absolute", right: -70, top: -50, opacity: 0.16, pointerEvents: "none" }}
        width="320"
        height="320"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#fff"
        strokeWidth="0.5"
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20M12 2c4 3 4 17 0 20M12 2c-4 3-4 17 0 20" />
      </svg>
      <svg
        aria-hidden="true"
        style={{
          position: "absolute",
          left: -60,
          bottom: 220,
          opacity: 0.1,
          pointerEvents: "none",
        }}
        width="220"
        height="220"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#fff"
        strokeWidth="0.5"
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20M12 2c4 3 4 17 0 20" />
      </svg>

      <div
        style={{
          padding: "0 26px 16px",
          paddingTop: "max(env(safe-area-inset-top, 12px), 12px)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            marginBottom: 32,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            position: "relative",
          }}
        >
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              aria-label={t("nav.back", { defaultValue: "Back" })}
              style={{
                position: "absolute",
                insetInlineStart: 0,
                top: "50%",
                transform: "translateY(-50%)",
                width: 36,
                height: 36,
                border: 0,
                background: "transparent",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: 0,
                flexShrink: 0,
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
                  stroke="#fff"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
          <span
            style={{
              fontSize: 16,
              fontWeight: 800,
              letterSpacing: "0.12em",
              color: "#fff",
            }}
          >
            RASID
          </span>
        </div>

        <h1
          style={{
            fontSize: 32,
            fontWeight: 800,
            lineHeight: 1.1,
            letterSpacing: -0.5,
            margin: 0,
            color: "#fff",
            textAlign: "center",
          }}
        >
          {t("lang.pickTitle")}
        </h1>
        <p
          style={{
            fontSize: 14,
            opacity: 0.92,
            lineHeight: 1.5,
            maxWidth: 320,
            margin: "10px auto 0",
            color: "#fff",
            textAlign: "center",
          }}
        >
          {t("lang.pickBody")}
        </p>
      </div>

      <div
        style={{
          background: "var(--c-card)",
          color: "var(--c-ink)",
          marginTop: "auto",
          borderTopLeftRadius: 28,
          borderTopRightRadius: 28,
          paddingTop: 20,
          paddingLeft: 18,
          paddingRight: 18,
          boxShadow: "0 -10px 28px rgba(8,40,80,0.22)",
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
        }}
      >
        {/* Scrollable so the continue button stays on screen. */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            overflowY: "auto",
            flex: 1,
            minHeight: 0,
          }}
        >
          {LANGUAGES.map((lang) => {
            const isActive = (selected ?? "en") === lang.code;
            return (
              <button
                key={lang.code}
                type="button"
                onClick={() => setSelected(lang.code)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "14px",
                  borderRadius: 14,
                  border: isActive ? "2px solid var(--c-blue-700)" : "1.5px solid var(--c-line)",
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
                    <svg aria-hidden="true" width="11" height="11" viewBox="0 0 12 12" fill="none">
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
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: 15,
                      fontFamily: lang.code === "ar" ? "var(--font-arabic)" : "var(--font-ui)",
                    }}
                  >
                    {lang.native}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--c-ink-3)", marginTop: 1 }}>
                    {lang.english} · {t("lang.officialUn")}
                  </div>
                </div>

                <span
                  style={{
                    fontSize: 11,
                    color: "var(--c-ink-3)",
                    fontFamily: "var(--font-ui)",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                  }}
                >
                  {lang.code}
                </span>
              </button>
            );
          })}
        </div>

        <button
          type="button"
          onClick={handleContinue}
          style={{
            marginTop: 18,
            flexShrink: 0,
            width: "100%",
            padding: "15px 18px",
            borderRadius: 12,
            border: "none",
            background: "var(--c-blue-700)",
            color: "#fff",
            fontSize: 16,
            fontWeight: 700,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            fontFamily: "var(--font-ui)",
          }}
        >
          {t("lang.continue")}
          <ArrowRightIcon flipInRtl={false} />
        </button>

        <div
          style={{
            flexShrink: 0,
            textAlign: "center",
            fontSize: 10,
            color: "var(--c-ink-4)",
            marginTop: 12,
            paddingBottom: "max(env(safe-area-inset-bottom, 22px), 22px)",
          }}
        >
          {t("lang.footer")}
        </div>
      </div>
    </div>
  );
}
