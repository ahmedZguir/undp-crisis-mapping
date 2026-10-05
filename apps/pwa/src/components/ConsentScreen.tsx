import { useTranslation } from "react-i18next";
import { recordConsent } from "../lib/consent";

// Mandatory consent gate after the language picker; a POLICY_VERSION bump re-shows it.

interface ConsentScreenProps {
  onAgree: () => void;
  onReadPolicy: () => void;
}

export function ConsentScreen({ onAgree, onReadPolicy }: ConsentScreenProps) {
  const { t } = useTranslation();

  const points: string[] = [
    t("consent.point.anonymous", {
      defaultValue: "You stay anonymous: no name, email, or login.",
    }),
    t("consent.point.collect", {
      defaultValue: "A report includes your photo, its location, and what you type.",
    }),
    t("consent.point.whoSees", {
      defaultValue:
        "Response coordinators see full reports; the public map shows only combined area totals.",
    }),
    t("consent.point.delete", {
      defaultValue: "You can delete everything you have submitted at any time in Settings.",
    }),
  ];

  const handleAgree = () => {
    recordConsent();
    onAgree();
  };

  return (
    <div
      style={{
        minHeight: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "linear-gradient(180deg, var(--c-blue-700) 0%, var(--c-blue-600) 100%)",
        fontFamily: "var(--font-ui)",
        color: "#fff",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "0 26px 16px",
          paddingTop: "max(env(safe-area-inset-top, 12px), 12px)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div style={{ marginBottom: 28 }}>
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
            fontSize: 30,
            fontWeight: 800,
            lineHeight: 1.12,
            letterSpacing: -0.5,
            margin: 0,
            color: "#fff",
            textAlign: "center",
          }}
        >
          {t("consent.title", { defaultValue: "Before you start" })}
        </h1>
        <p
          style={{
            fontSize: 14,
            opacity: 0.92,
            lineHeight: 1.5,
            maxWidth: 360,
            margin: "10px auto 0",
            color: "#fff",
            textAlign: "center",
          }}
        >
          {t("consent.body", {
            defaultValue:
              "Please read how your information is handled. By continuing you confirm you have read and agree to our privacy notice.",
          })}
        </p>
      </div>

      <div
        style={{
          background: "var(--c-card)",
          color: "var(--c-ink)",
          marginTop: "auto",
          borderTopLeftRadius: 28,
          borderTopRightRadius: 28,
          padding: "22px 20px max(env(safe-area-inset-bottom, 22px), 22px)",
          boxShadow: "0 -10px 28px rgba(8,40,80,0.22)",
          display: "flex",
          flexDirection: "column",
          gap: 0,
          textAlign: "start",
        }}
      >
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          {points.map((point, i) => (
            <li key={point} style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span
                aria-hidden
                style={{
                  flexShrink: 0,
                  fontSize: 14,
                  fontWeight: 700,
                  color: "var(--c-blue-700)",
                  fontFamily: "var(--font-ui)",
                  lineHeight: 1.45,
                  minWidth: 18,
                }}
              >
                {i + 1}.
              </span>
              <span style={{ fontSize: 14, lineHeight: 1.45, color: "var(--c-ink-2)", flex: 1 }}>
                {point}
              </span>
            </li>
          ))}
        </ul>

        <button
          type="button"
          onClick={onReadPolicy}
          style={{
            alignSelf: "flex-start",
            marginTop: 16,
            padding: 0,
            border: "none",
            background: "none",
            color: "var(--c-blue-700)",
            fontSize: 13,
            fontWeight: 700,
            textDecoration: "underline",
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
          }}
        >
          {t("consent.readFull", { defaultValue: "Read the full privacy notice" })}
        </button>

        <button
          type="button"
          onClick={handleAgree}
          style={{
            marginTop: 18,
            width: "100%",
            padding: "15px 18px",
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
          {t("consent.agree", { defaultValue: "I agree and continue" })}
        </button>

        <div style={{ textAlign: "center", fontSize: 11, color: "var(--c-ink-4)", marginTop: 12 }}>
          {t("consent.voluntary", { defaultValue: "Using the app is voluntary." })}
        </div>
      </div>
    </div>
  );
}
