/** Streaming summary for the Dashboard: owns the SSE iterator over `streamSummary`. */
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { getAdminReportDetail } from "../../../api/admin";
import {
  type ExemplarSnapshot,
  type SearchRequest,
  type SummaryClusterFrame,
  type SummaryScopeFrame,
  streamSummary,
} from "../../../api/search";
import { readSummaryCache, summarySignature, writeSummaryCache } from "../../../lib/adminWorkspace";

type SummaryFrameBody =
  | { kind: "prose"; text: string }
  | (SummaryScopeFrame & { kind: "scope" })
  | (SummaryClusterFrame & { kind: "cluster" })
  | { kind: "meta"; text: string }
  | { kind: "error"; message: string };

type FrameWithId = SummaryFrameBody & { id: number };

// Clusters stream in completion order; once closed, settle to scope, clusters by count, then meta.
function frameOrder(f: FrameWithId): number {
  if (f.kind === "scope") return 0;
  if (f.kind === "meta") return 2;
  return 1;
}

function reorderFrames(frames: FrameWithId[]): FrameWithId[] {
  return [...frames].sort((a, b) => {
    const delta = frameOrder(a) - frameOrder(b);
    if (delta !== 0) return delta;
    if (a.kind === "cluster" && b.kind === "cluster") return b.count - a.count;
    return 0;
  });
}

// Does not tick: just enough to flag a restored summary as a snapshot.
function generatedAgo(at: number, now: number): string {
  const secs = Math.max(0, Math.round((now - at) / 1000));
  if (secs < 45) return "Generated just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `Generated ${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `Generated ${hrs} hour${hrs === 1 ? "" : "s"} ago`;
  const days = Math.round(hrs / 24);
  return `Generated ${days} day${days === 1 ? "" : "s"} ago`;
}

interface Props {
  crisisId: string;
  filter: SearchRequest;
  // True once the parent has a non-empty result set.
  ready: boolean;
  onCiteClick?: (reportId: string) => void;
  // Fires on an actual run, so the onboarding checklist ticks on the action, not on opening the tab.
  onSummarised?: () => void;
}

export function SummaryPanel({ crisisId, filter, ready, onCiteClick, onSummarised }: Props) {
  const [frames, setFrames] = useState<FrameWithId[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [firstFrameReceived, setFirstFrameReceived] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Epoch ms of the cached summary on screen; null while streaming or when nothing is cached.
  const [generatedAt, setGeneratedAt] = useState<number | null>(null);
  const frameIdRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const [proseStreaming, setProseStreaming] = useState(false);
  const proseStreamingRef = useRef(false);
  proseStreamingRef.current = proseStreaming;

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // On crisis change, restore the cached summary only if it was generated under the same
  // filter, so it can't pass for another scope's data; otherwise clear.
  // biome-ignore lint/correctness/useExhaustiveDependencies: crisisId is the trigger; `filter` is read for the current value at switch time, intentionally not a dep (it changes on every pan)
  useEffect(() => {
    abortRef.current?.abort();
    setError(null);
    setStreaming(false);
    setProseStreaming(false);
    setFirstFrameReceived(false);
    const cached = readSummaryCache<FrameWithId>(crisisId);
    if (cached && cached.sig === summarySignature(filter)) {
      // Keep new frame ids ahead of restored ones so React keys don't collide.
      for (const f of cached.frames) frameIdRef.current = Math.max(frameIdRef.current, f.id);
      setFrames(cached.frames);
      setGeneratedAt(cached.generatedAt);
    } else {
      setFrames([]);
      setGeneratedAt(null);
    }
  }, [crisisId]);

  const run = useCallback(async () => {
    if (!ready) return;
    onSummarised?.();
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setFrames([]);
    setError(null);
    setGeneratedAt(null);
    setFirstFrameReceived(false);
    setStreaming(true);
    setProseStreaming(true);
    // Local mirror for caching: caching from inside a state updater would double-fire under StrictMode.
    let rendered: FrameWithId[] = [];
    let hadError = false;
    try {
      for await (const frame of streamSummary(crisisId, filter, ctrl.signal)) {
        if (ctrl.signal.aborted) break;
        setFirstFrameReceived(true);
        const id = ++frameIdRef.current;
        // Prose frames carry cumulative text: replace the last one in place.
        if (frame.kind === "prose") {
          const last = rendered[rendered.length - 1];
          rendered =
            last && last.kind === "prose"
              ? [...rendered.slice(0, -1), { ...frame, id: last.id }]
              : [...rendered, { ...frame, id }];
        } else {
          rendered = [...rendered, { ...frame, id }];
        }
        setFrames(rendered);
        // Any structured (scope/cluster) frame suppresses the prose cursor.
        if (frame.kind === "scope") setProseStreaming(false);
        if (frame.kind === "cluster") setProseStreaming(false);
        if (frame.kind === "meta") setProseStreaming(false);
        if (frame.kind === "error") {
          hadError = true;
          setError(frame.message);
          break;
        }
      }
    } catch (e) {
      if (!ctrl.signal.aborted) {
        hadError = true;
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setStreaming(false);
      setProseStreaming(false);
      const settled = reorderFrames(rendered);
      setFrames(settled);
      // Cache only a clean, complete, non-empty result, never an aborted or failed one.
      if (!ctrl.signal.aborted && !hadError && settled.length > 0) {
        const at = Date.now();
        setGeneratedAt(at);
        writeSummaryCache<FrameWithId>(crisisId, summarySignature(filter), settled, at);
      }
    }
  }, [crisisId, filter, ready, onSummarised]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(false);
    setProseStreaming(false);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", padding: "16px 18px", gap: 10 }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
        }}
      >
        <span
          className={streaming ? "pulse-soft" : ""}
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 0.06,
            textTransform: "uppercase",
            color: streaming ? "var(--c-blue-700)" : "var(--c-ink-3)",
            flex: 1,
          }}
        >
          Hierarchical AI summary
        </span>
        {streaming ? (
          <button
            type="button"
            className="btn secondary"
            onClick={cancel}
            style={{ padding: "5px 12px", fontSize: 11 }}
          >
            Cancel
          </button>
        ) : (
          <button
            type="button"
            className="btn primary"
            onClick={run}
            disabled={!ready}
            style={{ padding: "5px 12px", fontSize: 11 }}
          >
            {frames.length === 0 ? "Summarise" : "Regenerate"}
          </button>
        )}
      </header>

      {!streaming && generatedAt !== null && frames.length > 0 && !error && (
        <div style={{ fontSize: 10.5, color: "var(--c-ink-3)", marginTop: -2, marginBottom: 2 }}>
          {generatedAgo(generatedAt, Date.now())}. Regenerate for the current view.
        </div>
      )}

      {!ready && frames.length === 0 && !streaming && (
        <EmptyHint
          icon={SUMMARY_GLYPH}
          title="Run a search first"
          body="Then Summarise will group the matching reports into themes here."
        />
      )}
      {ready && frames.length === 0 && !streaming && !error && (
        <EmptyHint
          icon={SUMMARY_GLYPH}
          title="No summary yet"
          body="Click Summarise to cluster the filtered reports into themes. They'll appear here."
        />
      )}

      {streaming && !firstFrameReceived && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            color: "var(--c-blue-700)",
            fontSize: 12,
            padding: "16px 4px",
          }}
        >
          <span className="dots">
            <span /> <span /> <span />
          </span>
          <span>Reading the filtered reports…</span>
        </div>
      )}

      {error && (
        <ErrorBanner
          message={error}
          onRetry={() => {
            setError(null);
            run();
          }}
        />
      )}

      {frames.map((f, idx) => (
        <Frame
          key={f.id}
          frame={f}
          isLast={idx === frames.length - 1}
          streaming={streaming && proseStreamingRef.current}
          onCiteClick={onCiteClick}
        />
      ))}
    </div>
  );
}

function Frame({
  frame,
  isLast,
  streaming,
  onCiteClick,
}: {
  frame: FrameWithId;
  isLast: boolean;
  streaming: boolean;
  onCiteClick?: (reportId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // null = not yet fetched; Record once fetched (values may still be null for photo-less reports)
  const [photoUrls, setPhotoUrls] = useState<Record<string, string | null> | null>(null);

  useEffect(() => {
    if (!expanded || frame.kind !== "cluster" || photoUrls !== null) return;
    Promise.all(
      frame.exemplars.map((ex) =>
        getAdminReportDetail(ex.id)
          .then((d) => [ex.id, d.photo_url] as const)
          .catch(() => [ex.id, null] as const),
      ),
    ).then((pairs) => setPhotoUrls(Object.fromEntries(pairs)));
  }, [expanded, frame, photoUrls]);

  if (frame.kind === "prose") {
    return (
      <div className="fade-in" style={{ fontSize: 13, lineHeight: 1.5, color: "var(--c-ink)" }}>
        <span style={{ whiteSpace: "pre-wrap" }}>{frame.text}</span>
        {isLast && streaming && <span className="caret" aria-hidden="true" />}
      </div>
    );
  }
  if (frame.kind === "scope") {
    // Clustering runs on a sample, but the aggregates cover the full set: state the matched total.
    const total = frame.total.toLocaleString();
    const caption = `Summarised ${total} matched reports.`;
    return (
      <div
        className="fade-in"
        style={{ fontSize: 11, color: "var(--c-ink-3)", lineHeight: 1.4, fontStyle: "italic" }}
      >
        {caption}
      </div>
    );
  }
  if (frame.kind === "cluster") {
    return (
      <div
        className="fade-in"
        style={{
          background: "var(--c-blue-50)",
          border: "1px solid var(--c-line)",
          borderRadius: 10,
          padding: "10px 12px",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 4,
          }}
        >
          <span style={{ fontWeight: 700, color: "var(--c-blue-800)", fontSize: 13 }}>
            {frame.label}
          </span>
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: "var(--c-blue-700)",
              background: "#fff",
              border: "1px solid var(--c-blue-300)",
              padding: "1px 7px",
              borderRadius: 999,
            }}
          >
            {frame.count}
          </span>
        </div>
        <div
          style={{
            fontSize: 12.5,
            lineHeight: 1.45,
            color: "var(--c-ink)",
            whiteSpace: "pre-wrap",
          }}
        >
          {frame.sentences}
        </div>
        {frame.exemplars.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <button
              type="button"
              onClick={() => setExpanded((e) => !e)}
              style={{
                background: "none",
                border: "none",
                padding: 0,
                cursor: "pointer",
                fontSize: 10,
                color: "var(--c-blue-700)",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span>{expanded ? "▾" : "▸"}</span>
              <span>
                {frame.exemplars.length} example report
                {frame.exemplars.length !== 1 ? "s" : ""}
              </span>
            </button>
            {expanded && (
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                {frame.exemplars.map((ex) => (
                  <ExemplarCard
                    key={ex.id}
                    exemplar={ex}
                    photoUrl={photoUrls ? (photoUrls[ex.id] ?? null) : undefined}
                    onCiteClick={onCiteClick}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }
  if (frame.kind === "meta") {
    return (
      <div
        className="fade-in"
        style={{
          marginTop: 6,
          padding: "10px 12px",
          background: "var(--c-blue-100)",
          borderRadius: 10,
          fontSize: 13,
          fontWeight: 600,
          color: "var(--c-blue-900)",
          lineHeight: 1.4,
        }}
      >
        {frame.text}
      </div>
    );
  }
  return (
    <div
      style={{
        background: "var(--c-danger-bg)",
        color: "var(--c-danger)",
        border: "1px solid var(--c-danger)",
        borderRadius: 8,
        padding: "8px 12px",
        fontSize: 12,
      }}
    >
      {frame.message}
    </div>
  );
}

const DAMAGE_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  complete: { bg: "var(--c-danger-bg)", color: "var(--c-danger)", label: "● complete" },
  partial: { bg: "#fff8e1", color: "#e6a000", label: "● partial" },
  minimal: { bg: "#f0fdf4", color: "#16a34a", label: "● minimal" },
};

function ExemplarCard({
  exemplar,
  photoUrl,
  onCiteClick,
}: {
  exemplar: ExemplarSnapshot;
  /** undefined = still loading; null = loaded, no photo */
  photoUrl?: string | null;
  onCiteClick?: (id: string) => void;
}) {
  const style = DAMAGE_STYLE[exemplar.damage_class ?? ""] ?? DAMAGE_STYLE.minimal;
  const text = exemplar.description ?? "—";
  const truncated = text.length > 90 ? `${text.slice(0, 90)}…` : text;
  return (
    <button
      type="button"
      onClick={() => onCiteClick?.(exemplar.id)}
      disabled={!onCiteClick}
      style={{
        background: "#fff",
        border: "1px solid var(--c-line)",
        borderRadius: 6,
        padding: "6px 8px",
        cursor: onCiteClick ? "pointer" : "default",
        textAlign: "left",
        display: "flex",
        gap: 8,
        alignItems: "flex-start",
        width: "100%",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            background: style.bg,
            color: style.color,
            padding: "1px 5px",
            borderRadius: 999,
            whiteSpace: "nowrap",
            marginBottom: 3,
            display: "inline-block",
          }}
        >
          {style.label}
        </span>
        <div style={{ fontSize: 11, color: "var(--c-ink)", lineHeight: 1.35 }}>{truncated}</div>
        {exemplar.building_name && (
          <div style={{ fontSize: 10, color: "var(--c-ink-3)", marginTop: 2 }}>
            {exemplar.building_name}
          </div>
        )}
      </div>
      {/* Photo slot: shimmer while loading, image or nothing once resolved */}
      {photoUrl !== null && (
        <div
          style={{
            width: 52,
            height: 52,
            borderRadius: 5,
            flexShrink: 0,
            overflow: "hidden",
            background: "var(--c-blue-50)",
          }}
        >
          {photoUrl === undefined ? (
            <div className="skeleton" style={{ width: "100%", height: "100%", borderRadius: 5 }} />
          ) : (
            <img
              src={photoUrl}
              alt=""
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          )}
        </div>
      )}
    </button>
  );
}

// Text lines with a sparkle accent, keeping the "AI-generated" cue.
const SUMMARY_GLYPH = (
  <svg
    viewBox="0 0 24 24"
    width="20"
    height="20"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M4 12h8" />
    <path d="M4 16h12" />
    <path d="M4 20h6" />
    <path
      d="M17.5 2.5 18.28 4.72 20.5 5.5 18.28 6.28 17.5 8.5 16.72 6.28 14.5 5.5 16.72 4.72Z"
      fill="currentColor"
      stroke="none"
    />
  </svg>
);

function EmptyHint({ icon, title, body }: { icon: ReactNode; title: string; body?: string }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
        padding: "28px 16px",
        textAlign: "center",
        background: "var(--c-surface)",
        borderRadius: 10,
        border: "1px dashed var(--c-line)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 36,
          height: 36,
          borderRadius: 999,
          background: "var(--c-blue-100)",
          color: "var(--c-blue-700)",
          display: "grid",
          placeItems: "center",
          fontSize: 18,
        }}
      >
        {icon}
      </span>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-ink)" }}>{title}</div>
      {body && (
        <div style={{ fontSize: 12, color: "var(--c-ink-3)", maxWidth: 320, lineHeight: 1.4 }}>
          {body}
        </div>
      )}
    </div>
  );
}

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      style={{
        background: "var(--c-danger-bg)",
        border: "1px solid var(--c-danger)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <div style={{ flex: 1, fontSize: 12, color: "var(--c-danger)" }}>{message}</div>
      <button
        type="button"
        className="btn secondary"
        onClick={onRetry}
        style={{ padding: "4px 10px", fontSize: 11 }}
      >
        Retry
      </button>
    </div>
  );
}
