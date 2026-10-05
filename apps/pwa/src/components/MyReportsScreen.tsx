import type { TFunction } from "i18next";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { type Badge, getReporterStats } from "../api/reporter";
import {
  type CitizenReportHistoryItem,
  type ReportQuality,
  deleteReport,
  getCitizenReportHistory,
} from "../api/reports";
import { useDrafts } from "../hooks/useDrafts";
import { useGatedImageSrc } from "../hooks/useGatedImageSrc";
import { useIsRtl } from "../hooks/useIsRtl";
import { useOutbox } from "../hooks/useOutbox";
import { useReportThumb } from "../hooks/useReportThumb";
import { useReporterStats } from "../hooks/useReporterStats";
import { getClientId, peekClientId } from "../lib/clientId";
import { loadDrafts, loadOutbox } from "../lib/db";
import { formatRelativeTime } from "../lib/relativeTime";
import {
  type NoticeInputs,
  TAB_BADGE_BG,
  type TabBadgeColor,
  type UnseenSets,
  computeUnseen,
  markSeen,
  readSeen,
} from "../lib/reportNotice";
import {
  REPORTS_CACHE_UPDATED_EVENT,
  loadCachedReports,
  refreshReportsCache,
  saveCachedReports,
} from "../lib/reportsCache";
import { outbox as outboxAdapter } from "../platform/outbox";
import { BadgeIcon } from "./BadgeIcon";
import { DraftCard } from "./DraftCard";
import { MyBadgesSheet } from "./MyBadgesSheet";
import { ConfirmDialog, DIALOG_BACKDROP_STYLE } from "./ui/ConfirmDialog";

interface MyReportsScreenProps {
  onBack: () => void;
  onContinueDraft: (id: string) => void;
  onDiscardDraft: (id: string) => void;
  onNewReport: () => void;
}

// Lock each report's photo URL for the session: Supabase re-signs on every fetch, and swapping
// <img src> forces a re-download + re-decode. Released per id on load error so a fresh URL is adopted.
const sessionPhotoUrls = new Map<string, string>();
function stablePhotoUrl(id: string, fresh: string | null | undefined): string | null {
  if (fresh && !sessionPhotoUrls.has(id)) sessionPhotoUrls.set(id, fresh);
  return sessionPhotoUrls.get(id) ?? fresh ?? null;
}
function releasePhotoUrl(id: string): void {
  sessionPhotoUrls.delete(id);
}

// Minimum gap between stale-photo list refetches, so a broken image can't loop.
const STALE_PHOTO_RETRY_THROTTLE_MS = 5000;
const TOAST_MS = 3000;

function damageLabel(d: CitizenReportHistoryItem["damage_class"] | null, t: TFunction): string {
  if (d === "minimal") return t("damageClass.minimal");
  if (d === "partial") return t("damageClass.partial");
  if (d === "complete") return t("damageClass.complete");
  return "—";
}

// Map known DB enum values to labels; otherwise de-underscore so raw enums never reach citizens.
function crisisTypeLabel(type: string, t: TFunction): string {
  if (type === "natural_hazards") return t("stepInfraDetails.natural");
  if (type === "technological") return t("stepInfraDetails.technological");
  if (type === "human_made") return t("stepInfraDetails.humanMade");
  return type.replace(/_/g, " ");
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; items: CitizenReportHistoryItem[]; from_cache?: boolean; cached_at?: number }
  | { kind: "error" };

export function MyReportsScreen({
  onBack,
  onContinueDraft,
  onDiscardDraft,
  onNewReport,
}: MyReportsScreenProps) {
  const { t } = useTranslation();
  const isRtl = useIsRtl();
  const drafts = useDrafts();
  const outbox = useOutbox();
  const { stats: reporterStats, refetch: refetchStats } = useReporterStats();
  const [badgesOpen, setBadgesOpen] = useState(false);
  // Hydrate synchronously from the per-client cache to avoid flashing an empty list;
  // the async effect below covers the cold case where peekClientId is not ready.
  const [history, setHistory] = useState<LoadState>(() => {
    const id = peekClientId();
    const cached = id ? loadCachedReports(id) : null;
    return cached ? { kind: "loaded", items: cached.items } : { kind: "idle" };
  });

  // Bumped only on stale-URL recovery; resets each row's photo-failed flag.
  const [photoEpoch, setPhotoEpoch] = useState(0);

  // De-dup near-simultaneous fetch triggers into one network call.
  const inflightRef = useRef<Promise<void> | null>(null);

  const fetchHistory = useCallback(async () => {
    if (inflightRef.current) return inflightRef.current;
    const p = (async () => {
      setHistory((prev) => (prev.kind === "loaded" ? prev : { kind: "loading" }));
      try {
        const clientId = await getClientId();
        const { items } = await getCitizenReportHistory(clientId);
        saveCachedReports(clientId, items);
        setHistory({ kind: "loaded", items });
        // Do not bump photoEpoch here: re-signed URLs are equivalent, and remounting every <img>
        // is the churn stablePhotoUrl avoids.
      } catch {
        // Offline / API unreachable: fall back to the local cache.
        const clientId = await getClientId().catch(() => null);
        const cached = clientId ? loadCachedReports(clientId) : null;
        if (cached) {
          setHistory({
            kind: "loaded",
            items: cached.items,
            from_cache: true,
            cached_at: cached.cached_at,
          });
        } else {
          setHistory({ kind: "error" });
        }
      }
    })();
    inflightRef.current = p;
    try {
      await p;
    } finally {
      inflightRef.current = null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const clientId = await getClientId().catch(() => null);
      if (cancelled || !clientId) return;
      const cached = loadCachedReports(clientId);
      if (cached) {
        // Optimistic hydration, not a failure: no "showing cached" banner (set only in fetchHistory's catch).
        setHistory((prev) =>
          prev.kind === "loaded" ? prev : { kind: "loaded", items: cached.items },
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch on mount, foreground and reconnect. Do NOT poll.
  useEffect(() => {
    void fetchHistory();
    const onVisible = () => {
      if (document.visibilityState === "visible") void fetchHistory();
    };
    const onOnline = () => {
      void fetchHistory();
    };
    // The outbox flusher and useScoringWatch refresh the cache in the background; re-hydrate
    // so new rows appear without a screen-local poll.
    const onCacheUpdated = async () => {
      const clientId = await getClientId().catch(() => null);
      if (!clientId) return;
      const cached = loadCachedReports(clientId);
      if (!cached) return;
      setHistory({ kind: "loaded", items: cached.items });
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    window.addEventListener(REPORTS_CACHE_UPDATED_EVENT, onCacheUpdated);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
      window.removeEventListener(REPORTS_CACHE_UPDATED_EVENT, onCacheUpdated);
    };
  }, [fetchHistory]);

  // Refresh badges/points when the scored count rises (the worker may have awarded a badge).
  const scoredCount =
    history.kind === "loaded" ? history.items.filter((i) => i.quality?.scored).length : 0;
  const prevScoredRef = useRef(scoredCount);
  useEffect(() => {
    if (scoredCount > prevScoredRef.current) refetchStats();
    prevScoredRef.current = scoredCount;
  }, [scoredCount, refetchStats]);

  // A thumbnail 4xx means the signed URL is stale; refetch, throttled so a broken image cannot loop.
  const lastStaleRetryRef = useRef(0);
  const handleStalePhoto = useCallback(() => {
    const now = Date.now();
    if (now - lastStaleRetryRef.current < STALE_PHOTO_RETRY_THROTTLE_MS) return;
    lastStaleRetryRef.current = now;
    void fetchHistory().then(() => setPhotoEpoch((n) => n + 1));
  }, [fetchHistory]);

  const submittedCount = history.kind === "loaded" ? history.items.length : null;
  const queuedCount = outbox.queuedCount;
  const draftsCount = drafts.drafts.length;

  // The "seen" baseline is frozen at mount so per-row "new" markers last the whole visit even though
  // the tab dot is cleared on open. Null on first run means no dots.
  const [noticeBaseline] = useState(() => readSeen());
  const submittedIds = history.kind === "loaded" ? history.items.map((i) => i.id) : [];
  const newSets: UnseenSets | null = noticeBaseline
    ? computeUnseen(
        {
          submittedIds,
          badgeSlugs: (reporterStats?.badges ?? []).map((b) => b.slug),
          outbox: outbox.rows.map((r) => ({ id: r.id, status: r.status })),
          draftIds: drafts.drafts.map((d) => d.id),
        },
        noticeBaseline,
      )
    : null;

  // Loads a complete snapshot from the stores (not component state) so a partially hydrated
  // mount never writes an empty baseline.
  const markSeenNow = useCallback(async () => {
    const clientId = await getClientId().catch(() => null);
    if (!clientId) return;
    const [outboxRows, draftRows] = await Promise.all([loadOutbox(), loadDrafts()]);
    const cached = loadCachedReports(clientId);
    const stats = await getReporterStats(clientId).catch(() => null);
    const inputs: NoticeInputs = {
      submittedIds: (cached?.items ?? []).map((i) => i.id),
      badgeSlugs: (stats?.badges ?? []).map((b) => b.slug),
      outbox: outboxRows.map((r) => ({ id: r.id, status: r.status })),
      draftIds: draftRows.map((d) => d.id),
    };
    markSeen(inputs);
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: history/outbox/drafts/stats are intentional re-mark triggers — markSeenNow reloads its own snapshot.
  useEffect(() => {
    void markSeenNow();
  }, [markSeenNow, history, outbox.rows, drafts.drafts, reporterStats]);

  // The server delete is always-200 by contract: ignore the body and refetch history as the source of truth.
  const [pendingDelete, setPendingDelete] = useState<CitizenReportHistoryItem | null>(null);
  const [pendingDiscard, setPendingDiscard] = useState<string | null>(null);
  const [viewingReport, setViewingReport] = useState<CitizenReportHistoryItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = setTimeout(() => setToast(null), TOAST_MS);
  }, []);

  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    },
    [],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      const clientId = await getClientId();
      const outcome = await deleteReport(pendingDelete.id, clientId);
      if (outcome === "rate_limited") {
        showToast(t("myReports.delete.rateLimited"));
        return;
      }
      if (outcome === "error") {
        showToast(t("myReports.delete.error"));
        return;
      }
      // refreshReportsCache fires REPORTS_CACHE_UPDATED_EVENT, which this screen listens for.
      await refreshReportsCache();
      showToast(t("myReports.delete.success"));
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  }, [pendingDelete, showToast, t]);

  return (
    <div
      style={{
        height: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "var(--c-surface)",
        fontFamily: "var(--font-ui)",
        textAlign: "start",
      }}
    >
      <div
        style={{
          flexShrink: 0,
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
              stroke="var(--c-ink)"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ReportsIcon />
          <div style={{ fontSize: 16, fontWeight: 800, color: "var(--c-ink)" }}>
            {t("myReports.title")}
          </div>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 16px 76px" }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <StatCard
            label={t("myReports.statSubmitted")}
            value={submittedCount === null ? "—" : submittedCount}
          />
          <StatCard label={t("myReports.statQueued")} value={queuedCount} />
          <StatCard label={t("myReports.statDrafts")} value={draftsCount} />
        </div>

        <button
          type="button"
          onClick={onNewReport}
          style={{
            width: "100%",
            padding: "12px",
            borderRadius: 12,
            border: "none",
            background: "var(--c-blue-700, #1d4ed8)",
            color: "#fff",
            fontSize: 15,
            fontWeight: 700,
            cursor: "pointer",
            marginBottom: 18,
          }}
        >
          {t("myReports.new")}
        </button>

        <BadgesSection
          badges={reporterStats?.badges ?? []}
          hasNew={(newSets?.badges.size ?? 0) > 0}
          onOpen={() => setBadgesOpen(true)}
          t={t}
        />

        <Section title={t("myReports.drafts")} bare>
          {drafts.drafts.length === 0 ? (
            <Empty>{t("myReports.noDrafts")}</Empty>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {drafts.drafts.map((d) => (
                <DraftCard
                  key={d.id}
                  draft={d}
                  onContinue={() => onContinueDraft(d.id)}
                  onDiscard={() => setPendingDiscard(d.id)}
                />
              ))}
            </div>
          )}
        </Section>

        <Section
          title={t("myReports.queued")}
          action={
            outbox.queuedCount > 0 ? (
              <button
                type="button"
                onClick={() => void outboxAdapter.requestFlush("my-reports")}
                style={{
                  background: "transparent",
                  border: "1px solid var(--c-line)",
                  borderRadius: 8,
                  padding: "4px 10px",
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                {t("myReports.syncNow")}
              </button>
            ) : null
          }
        >
          {outbox.rows.length === 0 ? (
            <Empty>{t("myReports.nothingWaiting")}</Empty>
          ) : (
            outbox.rows.map((r) => {
              const isFailed = r.status === "failed";
              const isSubmitting = r.status === "submitting";
              const statusLabel = t(`outbox.status.${r.status}`, { defaultValue: r.status });
              const dotColor: TabBadgeColor | null = newSets
                ? isFailed && newSets.failed.has(r.id)
                  ? "red"
                  : newSets.queued.has(r.id)
                    ? "yellow"
                    : null
                : null;
              return (
                <Row
                  key={r.id}
                  title={r.payload.infra_name || t("myReports.untitled")}
                  subtitle={t("myReports.queuedStatus", {
                    status: statusLabel,
                    needsCrisis: r.payload.crisis_id ? "" : t("myReports.needsCrisis"),
                    attempts: r.attempt_count,
                  })}
                  leading={dotColor ? <NewDot color={dotColor} /> : undefined}
                  trailing={
                    <div
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "flex-end",
                        gap: 6,
                      }}
                    >
                      <Pill color={isFailed ? "#b91c1c" : "#b45309"}>{statusLabel}</Pill>
                      <div style={{ display: "flex", gap: 6 }}>
                        {isFailed && (
                          <OutboxAction
                            onClick={async () => {
                              const id = await outbox.retryAsDraft(r.id);
                              if (id) onContinueDraft(id);
                            }}
                          >
                            {t("outbox.editRetry", { defaultValue: "Edit & retry" })}
                          </OutboxAction>
                        )}
                        <OutboxAction
                          danger
                          disabled={isSubmitting}
                          onClick={() => void outbox.cancel(r.id)}
                        >
                          {isSubmitting
                            ? t("outbox.uploading", { defaultValue: "Uploading…" })
                            : t("outbox.cancel", { defaultValue: "Cancel" })}
                        </OutboxAction>
                      </div>
                    </div>
                  }
                />
              );
            })
          )}
        </Section>

        <Section
          title={t("myReports.submitted")}
          action={
            <button
              type="button"
              onClick={() => void fetchHistory()}
              disabled={history.kind === "loading"}
              style={{
                background: "transparent",
                border: "1px solid var(--c-line)",
                borderRadius: 8,
                padding: "4px 10px",
                fontSize: 12,
                cursor: history.kind === "loading" ? "default" : "pointer",
                opacity: history.kind === "loading" ? 0.6 : 1,
              }}
            >
              {history.kind === "loading" ? t("myReports.refreshing") : t("myReports.refresh")}
            </button>
          }
        >
          {history.kind === "idle" || history.kind === "loading" ? (
            <Empty>{t("myReports.loading")}</Empty>
          ) : history.kind === "error" ? (
            <Empty>{t("myReports.couldNotLoad")}</Empty>
          ) : history.items.length === 0 ? (
            <Empty>{t("myReports.noSubmitted")}</Empty>
          ) : (
            <>
              {history.from_cache && (
                <div
                  style={{
                    padding: "8px 12px",
                    fontSize: 11,
                    color: "var(--c-ink-3)",
                    background: "#fef3c7",
                    borderBottom: "1px solid var(--c-line)",
                  }}
                >
                  {t("myReports.cachedNotice", {
                    when: history.cached_at
                      ? t("myReports.cachedFrom", {
                          relative: formatRelativeTime(history.cached_at, t, "myReports"),
                        })
                      : "",
                  })}
                </div>
              )}
              {history.items.map((item) => (
                <SubmittedRow
                  key={item.id}
                  item={item}
                  photoEpoch={photoEpoch}
                  isNew={newSets?.submitted.has(item.id) ?? false}
                  onStalePhoto={handleStalePhoto}
                  onDelete={() => setPendingDelete(item)}
                  onView={() => setViewingReport(item)}
                  t={t}
                />
              ))}
            </>
          )}
        </Section>
      </div>

      {viewingReport && (
        <ReportDetailModal
          item={viewingReport}
          photoEpoch={photoEpoch}
          onClose={() => setViewingReport(null)}
          t={t}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          titleId="delete-report-title"
          title={t("myReports.delete.confirmTitle")}
          body={t("myReports.delete.confirmBody")}
          cancelLabel={t("myReports.delete.cancel")}
          confirmLabel={deleting ? t("myReports.delete.deleting") : t("myReports.delete.confirm")}
          busy={deleting}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void confirmDelete()}
        />
      )}

      {pendingDiscard && (
        <ConfirmDialog
          titleId="discard-draft-title"
          title={t("draft.discardTitle")}
          body={t("draft.discardBody")}
          cancelLabel={t("draft.discardCancel")}
          confirmLabel={t("draft.discardConfirm")}
          onCancel={() => setPendingDiscard(null)}
          onConfirm={() => {
            onDiscardDraft(pendingDiscard);
            setPendingDiscard(null);
          }}
        />
      )}

      {toast && (
        <output
          aria-live="polite"
          style={{
            position: "fixed",
            left: "50%",
            transform: "translateX(-50%)",
            bottom: "calc(80px + env(safe-area-inset-bottom, 0px))",
            padding: "10px 16px",
            background: "rgba(17, 24, 39, 0.92)",
            color: "#fff",
            borderRadius: 999,
            fontSize: 13,
            fontWeight: 600,
            boxShadow: "0 6px 18px rgba(0,0,0,0.25)",
            zIndex: 2100,
            maxWidth: "calc(100vw - 32px)",
            textAlign: "center",
          }}
        >
          {toast}
        </output>
      )}

      {badgesOpen && <MyBadgesSheet stats={reporterStats} onClose={() => setBadgesOpen(false)} />}
    </div>
  );
}

interface SubmittedRowProps {
  item: CitizenReportHistoryItem;
  photoEpoch: number;
  isNew: boolean;
  onStalePhoto: () => void;
  onDelete: () => void;
  onView: () => void;
  t: TFunction;
}

function SubmittedRow({
  item,
  photoEpoch,
  isNew,
  onStalePhoto,
  onDelete,
  onView,
  t,
}: SubmittedRowProps) {
  const [photoFailed, setPhotoFailed] = useState(false);
  // A successful refetch may have produced a working URL.
  // biome-ignore lint/correctness/useExhaustiveDependencies: photoEpoch is the trigger; setPhotoFailed is the only side-effect.
  useEffect(() => {
    setPhotoFailed(false);
  }, [photoEpoch]);

  // Native behind the ngrok gate resolves via a credentialed fetch to a blob: URL. A native fetch
  // failure only marks the photo failed: a list refetch would loop without fixing the credential.
  const stableUrl = stablePhotoUrl(item.id, item.photo_url);
  const photo = useGatedImageSrc(stableUrl, photoEpoch, () => setPhotoFailed(true));
  // Missing/unreachable signed URL: fall back to the locally-cached thumbnail.
  const signedUnavailable = photoFailed || photo.failed || !item.photo_url;
  const thumbSrc = useReportThumb(item.id, signedUnavailable);

  const submittedAt = new Date(item.created_at).getTime();
  const title = item.infra_name || t("myReports.submittedReport");
  const subtitle = `${damageLabel(item.damage_class, t)} · ${formatRelativeTime(submittedAt, t, "myReports")}`;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "10px 12px",
        borderBottom: "1px solid var(--c-line)",
      }}
    >
      {isNew && <NewDot color="green" />}
      <button
        type="button"
        onClick={onView}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flex: 1,
          minWidth: 0,
          background: "transparent",
          border: "none",
          textAlign: "start",
          cursor: "pointer",
          padding: 0,
        }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 8,
            background: "#1a1a2a",
            flexShrink: 0,
            overflow: "hidden",
            display: "grid",
            placeItems: "center",
          }}
        >
          {!signedUnavailable && photo.src ? (
            <img
              src={photo.src}
              alt=""
              decoding="async"
              style={{ width: "100%", height: "100%", objectFit: "contain" }}
              onError={() => {
                releasePhotoUrl(item.id);
                setPhotoFailed(true);
                onStalePhoto();
              }}
            />
          ) : thumbSrc ? (
            <img
              src={thumbSrc}
              alt=""
              decoding="async"
              style={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
          ) : (
            <span style={{ color: "rgba(255,255,255,0.5)", fontSize: 9 }}>img</span>
          )}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--c-ink)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {title}
          </div>
          <div
            style={{
              fontSize: 12,
              color: "var(--c-ink-3)",
              marginTop: 2,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {subtitle}
          </div>
          <div
            style={{
              display: "flex",
              gap: 6,
              marginTop: 4,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <Pill color="#1d4ed8" subtle>
              {item.crisis_name}
            </Pill>
            {item.crisis_status === "archived" && (
              <Pill color="#6b7280" subtle>
                {t("myReports.archived")}
              </Pill>
            )}
            <QualityPills quality={item.quality} t={t} />
          </div>
        </div>
      </button>
      <button
        type="button"
        onClick={onDelete}
        aria-label={t("myReports.delete.aria")}
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

function QualityPills({ quality, t }: { quality: ReportQuality | null; t: TFunction }) {
  if (!quality) return null;
  if (!quality.scored) {
    return (
      <Pill color="#6b7280" subtle>
        {t("myReports.quality.analyzing", { defaultValue: "Analyzing…" })}
      </Pill>
    );
  }
  return (
    <>
      <Pill color="#b5801a" subtle>
        {quality.points}/{quality.max_points}{" "}
        {t("myReports.quality.ptsShort", { defaultValue: "pts" })}
      </Pill>
      {quality.verified && (
        <Pill color="#15803d" subtle>
          {t("myReports.quality.verified", { defaultValue: "Verified by UNDP" })}
        </Pill>
      )}
      {quality.is_duplicate_image && (
        <Pill color="#6b7280" subtle>
          {t("myReports.quality.duplicate", { defaultValue: "Duplicate photo" })}
        </Pill>
      )}
    </>
  );
}

function FactorGlyph({ state }: { state: ReportQuality["factors"][number]["state"] }) {
  const map = {
    earned: { color: "var(--c-safe, #15803d)", d: "M4 10l4 4 8-9" },
    missed: { color: "var(--c-ink-3)", d: "M5 5l10 10M15 5L5 15" },
    na: { color: "var(--c-ink-3)", d: "M5 10h10" },
  } as const;
  const { color, d } = map[state];
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d={d} stroke={color} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function QualityBreakdown({ quality, t }: { quality: ReportQuality | null; t: TFunction }) {
  if (!quality) return null;
  const title = t("myReports.quality.title", { defaultValue: "Points" });
  if (!quality.scored) {
    return (
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--c-ink-2)", marginBottom: 6 }}>
          {title}
        </div>
        <div style={{ fontSize: 13, color: "var(--c-ink-3)", lineHeight: 1.4 }}>
          {t("myReports.quality.analyzingBody", {
            defaultValue: "We're still analyzing this report. Points appear once it's scored.",
          })}
        </div>
      </div>
    );
  }
  return (
    <div style={{ marginTop: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--c-ink-2)" }}>{title}</span>
        <span style={{ fontSize: 13, fontWeight: 700, color: "#b5801a" }}>
          {t("myReports.quality.pointsOfMax", {
            points: quality.points,
            max: quality.max_points,
            defaultValue: `${quality.points} of ${quality.max_points} points`,
          })}
        </span>
      </div>
      {quality.is_duplicate_image ? (
        <div style={{ fontSize: 13, color: "var(--c-ink-3)", lineHeight: 1.4 }}>
          {t("myReports.quality.duplicateBody", {
            defaultValue: "This photo matched another report, so it earned no points.",
          })}
        </div>
      ) : (
        quality.factors.map((f) => (
          <div
            key={f.key}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "5px 0",
              opacity: f.state === "na" ? 0.55 : 1,
            }}
          >
            <FactorGlyph state={f.state} />
            <span
              style={{
                fontSize: 13,
                color: "var(--c-ink)",
                textDecoration: f.state === "missed" ? "line-through" : "none",
              }}
            >
              {t(`myReports.factor.${f.key}`, { defaultValue: f.key })}
            </span>
            {f.state === "na" && (
              <span style={{ fontSize: 11, color: "var(--c-ink-3)" }}>
                {t("myReports.factor.na", { defaultValue: "not applicable" })}
              </span>
            )}
          </div>
        ))
      )}
      {quality.verified && (
        <div
          style={{ fontSize: 12, color: "var(--c-safe, #15803d)", fontWeight: 600, marginTop: 8 }}
        >
          {t("myReports.quality.verified", { defaultValue: "Verified by UNDP" })}
        </div>
      )}
    </div>
  );
}

function BadgesSection({
  badges,
  hasNew,
  onOpen,
  t,
}: {
  badges: Badge[];
  hasNew: boolean;
  onOpen: () => void;
  t: TFunction;
}) {
  return (
    <Section
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          {t("myStats.badges", { defaultValue: "Badges" })}
          {hasNew && <NewDot color="green" />}
          <button
            type="button"
            onClick={onOpen}
            aria-label={t("myStats.howToEarn", { defaultValue: "Badges & how to earn them" })}
            style={{
              width: 20,
              height: 20,
              padding: 0,
              border: "none",
              background: "transparent",
              cursor: "pointer",
              display: "inline-grid",
              placeItems: "center",
              color: "var(--c-ink-3)",
            }}
          >
            <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M10 2a8 8 0 100 16 8 8 0 000-16Zm0 14.5A6.5 6.5 0 1110 3.5a6.5 6.5 0 010 13ZM9 8.5h2v6H9v-6Zm0-3h2v2H9v-2Z" />
            </svg>
          </button>
        </span>
      }
      action={
        badges.length > 0 ? (
          <button
            type="button"
            onClick={onOpen}
            style={{
              background: "transparent",
              border: "1px solid var(--c-line)",
              borderRadius: 8,
              padding: "4px 10px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {t("myReports.viewAll", { defaultValue: "View all" })}
          </button>
        ) : null
      }
    >
      {badges.length === 0 ? (
        <Empty>{t("myReports.noBadges", { defaultValue: "Submit reports to earn badges." })}</Empty>
      ) : (
        <button
          type="button"
          onClick={onOpen}
          style={{
            display: "flex",
            gap: 8,
            padding: "12px",
            width: "100%",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            overflowX: "auto",
            WebkitOverflowScrolling: "touch",
          }}
        >
          {badges.map((b) => (
            <span
              key={b.slug}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 10px",
                borderRadius: 999,
                background: "linear-gradient(135deg, #fdf6e3, #f8eccd)",
                border: "1px solid #ead8a3",
                color: "#7a5d2c",
                fontSize: 12,
                fontWeight: 600,
                whiteSpace: "nowrap",
                flexShrink: 0,
              }}
            >
              <span style={{ color: "#b5801a", display: "inline-flex" }}>
                <BadgeIcon slug={b.slug} size={14} />
              </span>
              {t(`badges.${b.slug}`, { defaultValue: b.name })}
            </span>
          ))}
        </button>
      )}
    </Section>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "6px 0",
        borderBottom: "1px solid var(--c-line)",
        fontSize: 13,
      }}
    >
      <span
        style={{
          color: "var(--c-ink-3)",
          fontWeight: 600,
          flexShrink: 0,
          minWidth: 90,
          paddingTop: 1,
        }}
      >
        {label}
      </span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          color: "var(--c-ink)",
          lineHeight: 1.4,
        }}
      >
        {value}
      </span>
    </div>
  );
}

function ReportDetailModal({
  item,
  photoEpoch,
  onClose,
  t,
}: {
  item: CitizenReportHistoryItem;
  photoEpoch: number;
  onClose: () => void;
  t: TFunction;
}) {
  const [photoFailed, setPhotoFailed] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: photoEpoch resets failure state
  useEffect(() => {
    setPhotoFailed(false);
  }, [photoEpoch]);
  // Reuse the row's session-stable URL so the detail view does not re-download the photo.
  const stableUrl = stablePhotoUrl(item.id, item.photo_url);
  const photo = useGatedImageSrc(stableUrl, photoEpoch, () => setPhotoFailed(true));
  // Offline / stale-URL fallback to the locally-cached thumbnail (see SubmittedRow).
  const signedUnavailable = photoFailed || photo.failed || !item.photo_url;
  const thumbSrc = useReportThumb(item.id, signedUnavailable);
  const submittedAt = new Date(item.created_at).getTime();

  const rows: { label: string; value: string }[] = [];
  if (item.description)
    rows.push({
      label: t("stepDescription.label", { defaultValue: "Description" }),
      value: item.description,
    });
  if (item.infra_type && item.infra_type.length > 0)
    rows.push({
      label: t("stepInfraType.title", { defaultValue: "Type" }),
      value: item.infra_type.join(", "),
    });
  if (item.crisis_type) {
    const typeLabel = crisisTypeLabel(item.crisis_type, t);
    const val = item.crisis_type_detailed
      ? `${typeLabel} · ${item.crisis_type_detailed}`
      : typeLabel;
    rows.push({ label: t("stepCrisisNature.title", { defaultValue: "Crisis type" }), value: val });
  }
  rows.push({
    label: t("myReports.submitted"),
    value: `${new Date(item.created_at).toLocaleDateString()} · ${formatRelativeTime(submittedAt, t, "myReports")}`,
  });
  rows.push({
    label: t("browseMap.crisis", { defaultValue: "Crisis" }),
    value:
      item.crisis_status === "archived"
        ? `${item.crisis_name} (${t("myReports.archived")})`
        : item.crisis_name,
  });

  return (
    <dialog
      open
      aria-modal="true"
      aria-labelledby="report-detail-title"
      style={DIALOG_BACKDROP_STYLE}
    >
      <div
        style={{
          maxWidth: 400,
          width: "100%",
          maxHeight: "80vh",
          overflowY: "auto",
          background: "var(--c-card)",
          borderRadius: 16,
          boxShadow: "var(--c-shadow-3)",
          fontFamily: "var(--font-ui)",
          padding: "16px 16px 20px",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 12,
          }}
        >
          <h2
            id="report-detail-title"
            style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "var(--c-ink)" }}
          >
            {t("stepReview.title", { defaultValue: "Review" })}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("myReports.delete.cancel")}
            style={{
              width: 28,
              height: 28,
              border: 0,
              background: "transparent",
              color: "var(--c-ink-2)",
              fontSize: 18,
              lineHeight: 1,
              cursor: "pointer",
              display: "grid",
              placeItems: "center",
            }}
          >
            ×
          </button>
        </div>

        {/* Mirrors StepReview's header card */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: 10,
            border: "1px solid var(--c-line)",
            borderRadius: 12,
            marginBottom: 10,
          }}
        >
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 10,
              background: "#1a1a2a",
              flexShrink: 0,
              overflow: "hidden",
              display: "grid",
              placeItems: "center",
            }}
          >
            {!signedUnavailable && photo.src ? (
              <img
                src={photo.src}
                alt=""
                decoding="async"
                style={{ width: "100%", height: "100%", objectFit: "contain" }}
                onError={() => {
                  releasePhotoUrl(item.id);
                  setPhotoFailed(true);
                }}
              />
            ) : thumbSrc ? (
              <img
                src={thumbSrc}
                alt=""
                decoding="async"
                style={{ width: "100%", height: "100%", objectFit: "contain" }}
              />
            ) : (
              <span style={{ color: "rgba(255,255,255,0.5)", fontSize: 10 }}>no photo</span>
            )}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 15,
                fontWeight: 700,
                color: "var(--c-ink)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {item.infra_name || t("stepReview.unnamed", { defaultValue: "Unnamed location" })}
            </div>
            <div style={{ marginTop: 4 }}>
              <span
                style={{
                  display: "inline-block",
                  padding: "2px 8px",
                  borderRadius: 999,
                  background: "var(--c-warn-bg)",
                  color: "var(--c-warn, #92400e)",
                  fontSize: 11,
                  fontWeight: 700,
                }}
              >
                {damageLabel(item.damage_class, t)}
              </span>
            </div>
          </div>
        </div>

        {/* Field rows — same two-column layout as StepReview */}
        {rows.map((row) => (
          <DetailRow key={row.label} label={row.label} value={row.value} />
        ))}

        <QualityBreakdown quality={item.quality} t={t} />
      </div>
    </dialog>
  );
}

interface StatCardProps {
  label: string;
  value: number | string;
}

function StatCard({ label, value }: StatCardProps) {
  return (
    <div
      style={{
        flex: 1,
        padding: "10px 12px",
        borderRadius: 12,
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--c-ink-3)", fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: "var(--c-ink)", marginTop: 2 }}>
        {value}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
  action,
  bare = false,
}: {
  title: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  /** Render children directly, without the card wrapper. */
  bare?: boolean;
}) {
  return (
    <section style={{ marginBottom: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <h2
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: "var(--c-ink-2)",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            margin: 0,
          }}
        >
          {title}
        </h2>
        {action}
      </div>
      {bare ? (
        children
      ) : (
        <div
          style={{
            background: "var(--c-card)",
            borderRadius: 12,
            border: "1px solid var(--c-line)",
            overflow: "hidden",
          }}
        >
          {children}
        </div>
      )}
    </section>
  );
}

function Row({
  title,
  subtitle,
  trailing,
  leading,
}: {
  title: string;
  subtitle: string;
  trailing?: ReactNode;
  leading?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "12px",
        width: "100%",
        textAlign: "start",
        background: "transparent",
        border: "none",
        borderBottom: "1px solid var(--c-line)",
        cursor: "default",
      }}
    >
      {leading}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: "var(--c-ink)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {title}
        </div>
        <div
          style={{
            fontSize: 12,
            color: "var(--c-ink-3)",
            marginTop: 2,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {subtitle}
        </div>
      </div>
      {trailing}
    </div>
  );
}

function NewDot({ color }: { color: TabBadgeColor }) {
  return (
    <span
      aria-hidden="true"
      style={{
        width: 9,
        height: 9,
        borderRadius: 999,
        background: TAB_BADGE_BG[color],
        flexShrink: 0,
      }}
    />
  );
}

function OutboxAction({
  children,
  onClick,
  danger = false,
  disabled = false,
}: {
  children: ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{
        padding: "4px 10px",
        borderRadius: 8,
        border: danger ? 0 : "1px solid var(--c-line)",
        background: danger ? (disabled ? "var(--c-line)" : "var(--c-danger)") : "var(--c-card)",
        color: danger ? "#fff" : "var(--c-ink-2)",
        fontSize: 11,
        fontWeight: 700,
        cursor: disabled ? "not-allowed" : "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div
      style={{ padding: "14px 12px", fontSize: 13, color: "var(--c-ink-3)", textAlign: "center" }}
    >
      {children}
    </div>
  );
}

function ReportsIcon() {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 3h6a1 1 0 0 1 1 1v1h2a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h2V4a1 1 0 0 1 1-1z" />
      <path d="M9 5h6" />
      <path d="M8 11h8M8 15h5" />
    </svg>
  );
}

function Pill({
  color,
  children,
  subtle = false,
}: {
  color: string;
  children: ReactNode;
  subtle?: boolean;
}) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        background: `${color}${subtle ? "12" : "15"}`,
        color,
        fontSize: 11,
        fontWeight: subtle ? 600 : 700,
        textTransform: "capitalize",
        maxWidth: 180,
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        verticalAlign: "middle",
      }}
    >
      {children}
    </span>
  );
}
