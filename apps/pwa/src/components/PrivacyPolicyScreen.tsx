import { useTranslation } from "react-i18next";
import { useIsRtl } from "../hooks/useIsRtl";
import { recordConsent } from "../lib/consent";

// A body line starting with an en dash or hyphen renders as a bullet.
const BULLET_RE = /^[–-]\s*/;
const isBullet = (l: string) => BULLET_RE.test(l);
const stripBullet = (l: string) => l.replace(BULLET_RE, "");

function BodyText({ text }: { text: string }) {
  const blocks = text
    .split(/\n{2,}/)
    .map((b) => b.trim())
    .filter(Boolean);

  return (
    <>
      {blocks.map((block, bi) => {
        const lines = block.split("\n").map((l) => l.trim());
        const isList = lines.every(isBullet);
        if (isList) {
          return (
            <ul key={block} style={{ margin: "6px 0 0", paddingLeft: 18, listStyleType: "none" }}>
              {lines.map((line, li) => (
                <li
                  key={line}
                  style={{
                    fontSize: 14,
                    lineHeight: 1.6,
                    color: "var(--c-ink-2)",
                    marginTop: li === 0 ? 0 : 6,
                    paddingLeft: 8,
                    position: "relative",
                  }}
                >
                  <span
                    aria-hidden
                    style={{ position: "absolute", left: -10, color: "var(--c-ink-4)" }}
                  >
                    –
                  </span>
                  {stripBullet(line)}
                </li>
              ))}
            </ul>
          );
        }
        const parts = lines.map((line, li) => {
          return isBullet(line) ? (
            <span key={line} style={{ display: "block", paddingLeft: 14, position: "relative" }}>
              <span aria-hidden style={{ position: "absolute", left: 0, color: "var(--c-ink-4)" }}>
                –
              </span>
              {stripBullet(line)}
            </span>
          ) : (
            <span key={line} style={{ display: li > 0 ? "block" : undefined }}>
              {line}
            </span>
          );
        });
        return (
          <p
            key={block}
            style={{
              fontSize: 14,
              lineHeight: 1.6,
              color: "var(--c-ink-2)",
              marginTop: bi === 0 ? 6 : 10,
              marginBottom: 0,
            }}
          >
            {parts}
          </p>
        );
      })}
    </>
  );
}

// Canonical English text is docs/legal/privacy-policy.md; copy lives in public/locales.
const SECTIONS: ReadonlyArray<readonly [string, string]> = [
  ["privacy.who.title", "privacy.who.body"],
  ["privacy.noAccount.title", "privacy.noAccount.body"],
  ["privacy.collect.title", "privacy.collect.body"],
  ["privacy.why.title", "privacy.why.body"],
  ["privacy.whoSees.title", "privacy.whoSees.body"],
  ["privacy.risk.title", "privacy.risk.body"],
  ["privacy.rights.title", "privacy.rights.body"],
  ["privacy.security.title", "privacy.security.body"],
  ["privacy.retention.title", "privacy.retention.body"],
  ["privacy.changes.title", "privacy.changes.body"],
  ["privacy.questions.title", "privacy.questions.body"],
];

interface PrivacyPolicyScreenProps {
  onBack: () => void;
  // Consent-gate context: show an agree button after the policy.
  onAgree?: () => void;
}

export function PrivacyPolicyScreen({ onBack, onAgree }: PrivacyPolicyScreenProps) {
  const { t } = useTranslation();
  const isRtl = useIsRtl();

  const handleAgree = () => {
    recordConsent();
    onAgree?.();
  };

  return (
    <div
      style={{
        minHeight: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "var(--c-surface)",
        fontFamily: "var(--font-ui)",
        textAlign: "left",
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
          position: "sticky",
          top: 0,
          zIndex: 1,
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
        <div style={{ fontSize: 16, fontWeight: 800, color: "var(--c-ink)" }}>
          {t("privacy.title", { defaultValue: "Privacy notice" })}
        </div>
      </div>

      <div
        style={{
          padding:
            "18px calc(20px + env(safe-area-inset-right)) max(env(safe-area-inset-bottom, 28px), 28px) calc(20px + env(safe-area-inset-left))",
          maxWidth: 680,
          width: "100%",
          margin: "0 auto",
          boxSizing: "border-box",
        }}
      >
        <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--c-ink-2)", marginTop: 0 }}>
          {t("privacy.intro", {
            defaultValue:
              "This is a plain-language explanation of what happens to the information you share when you report damage through this app.",
          })}
        </p>
        <p style={{ fontSize: 11, color: "var(--c-ink-4)", marginTop: 4 }}>
          {t("privacy.lastUpdated", { defaultValue: "Last updated: 7 June 2026" })}
        </p>

        {SECTIONS.map(([titleKey, bodyKey]) => (
          <section key={titleKey} style={{ marginTop: 22 }}>
            <h2
              style={{
                fontSize: 15,
                fontWeight: 800,
                color: "var(--c-ink)",
                margin: 0,
              }}
            >
              {t(titleKey)}
            </h2>
            <BodyText text={t(bodyKey)} />
          </section>
        ))}

        {onAgree && (
          <button
            type="button"
            onClick={handleAgree}
            style={{
              marginTop: 32,
              marginBottom: "max(env(safe-area-inset-bottom, 0px), 0px)",
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
        )}
      </div>
    </div>
  );
}
