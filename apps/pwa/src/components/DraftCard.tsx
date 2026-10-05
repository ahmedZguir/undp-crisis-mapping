import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { type DraftRow, getPhoto } from "../lib/db";
import { formatRelativeTime } from "../lib/relativeTime";

interface DraftCardProps {
  draft: DraftRow;
  onContinue: () => void;
  onDiscard: () => void;
}

// Session cache of thumbnail object URLs by photo_id, so remounts do not re-read
// IndexedDB and flash the placeholder. Never revoked (drafts are few).
const thumbUrlCache = new Map<string, string>();

export function DraftCard({ draft, onContinue, onDiscard }: DraftCardProps) {
  const { t } = useTranslation();
  const [thumbUrl, setThumbUrl] = useState<string | null>(() =>
    draft.photo_id ? (thumbUrlCache.get(draft.photo_id) ?? null) : null,
  );

  useEffect(() => {
    const photoId = draft.photo_id;
    if (!photoId) {
      setThumbUrl(null);
      return;
    }
    const cached = thumbUrlCache.get(photoId);
    if (cached) {
      setThumbUrl(cached);
      return;
    }
    let cancelled = false;
    void (async () => {
      const blob = await getPhoto(photoId);
      if (cancelled || !blob) return;
      const url = URL.createObjectURL(blob);
      thumbUrlCache.set(photoId, url);
      setThumbUrl(url);
    })();
    // Not revoked: cached for the session (see thumbUrlCache).
    return () => {
      cancelled = true;
    };
  }, [draft.photo_id]);

  const damageLabel = draft.state.damage_class
    ? t(`damageClass.${draft.state.damage_class}`, {
        defaultValue: draft.state.damage_class,
      })
    : null;

  const infraName = draft.state.infra_name?.trim();
  const isUnnamed =
    !infraName || infraName === t("stepLocation.unnamed", { defaultValue: "Unnamed" });
  const locationLabel = isUnnamed ? t("draft.untitled", { defaultValue: "Draft" }) : infraName;
  // Lets users with drafts across crises tell them apart.
  const crisisName = draft.crisis?.name?.trim() || null;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: 10,
        border: "1px solid var(--c-line-2)",
        borderRadius: 12,
        background: "var(--c-card)",
      }}
    >
      <button
        type="button"
        onClick={onContinue}
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: "transparent",
          border: 0,
          padding: 0,
          cursor: "pointer",
          textAlign: "start",
          minWidth: 0,
        }}
        aria-label={t("draft.continueAria", {
          defaultValue: "Continue draft for {{location}}",
          location: locationLabel,
        })}
      >
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 8,
            background: "var(--c-blue-50)",
            backgroundImage: thumbUrl ? `url(${thumbUrl})` : undefined,
            backgroundSize: "cover",
            backgroundPosition: "center",
            flexShrink: 0,
            border: "1px solid var(--c-line-2)",
          }}
          aria-hidden
        />
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 700,
              color: "var(--c-ink)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {locationLabel}
          </div>
          {crisisName && (
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--c-danger)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {t("draft.forCrisis", { defaultValue: "Crisis: {{crisis}}", crisis: crisisName })}
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {damageLabel && (
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  padding: "2px 6px",
                  borderRadius: 999,
                  background: "var(--c-blue-50)",
                  color: "var(--c-blue-800)",
                }}
              >
                {damageLabel}
              </span>
            )}
            <span style={{ fontSize: 11, color: "var(--c-ink-3)" }}>
              {formatRelativeTime(draft.updated_at, t, "draft")}
            </span>
          </div>
        </div>
      </button>
      <button
        type="button"
        onClick={onDiscard}
        aria-label={t("draft.discardAria", { defaultValue: "Discard draft" })}
        title={t("draft.discardAria", { defaultValue: "Discard draft" })}
        style={{
          flexShrink: 0,
          padding: "6px 10px",
          borderRadius: 8,
          border: "1px solid var(--c-danger)",
          background: "transparent",
          color: "var(--c-danger)",
          fontSize: 12,
          fontWeight: 700,
          cursor: "pointer",
        }}
      >
        {t("myReports.delete.action")}
      </button>
    </div>
  );
}
