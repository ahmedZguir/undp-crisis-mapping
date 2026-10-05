/**
 * Multi-turn chat scoped to the active filter.
 *
 * - A filter change does not wipe the session; it keeps its locked-K context.
 * - When the scope drifts from session open, a sticky banner offers a new chat.
 * - Each assistant bubble shows `scope from HH:MM`.
 * - "New chat" wipes history and opens a fresh session on the current filter + bbox.
 */
import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  type ChatContextReport,
  type SearchRequest,
  chatTurn,
  openChat,
} from "../../../api/search";

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  citations?: string[];
  /** Server clock; rendered as `scope from HH:MM`. */
  answeredAt?: string | null;
  // Assistant message still awaiting the API (renders a typing indicator).
  pending?: boolean;
};

interface Props {
  crisisId: string;
  filter: SearchRequest;
  // Bumps on non-bbox filter changes; with `currentTotal`, drives the stale-scope banner.
  filterVersion: number;
  ready: boolean;
  /** Live `total_match_count`, compared against the count at session open. */
  currentTotal: number | null;
  /** The K-set at session open, or `[]` on a new chat; the map rings these reports. */
  onContextChange?: (reports: ChatContextReport[]) => void;
  /** Citation click: the map flies to the report, keeping the chat in view. */
  onCiteFocus?: (reportId: string) => void;
}

function formatScopeStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function ChatPanel({
  crisisId,
  filter,
  filterVersion,
  ready,
  currentTotal,
  onContextChange,
  onCiteFocus,
}: Props) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [history, setHistory] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [scope, setScope] = useState<{
    size: number;
    openedTotal: number;
    signature: string;
  } | null>(null);
  const [pending, setPending] = useState(false);
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Observed only for the stale banner; filter changes never invalidate the session.
  const sessionOpenedAtVersionRef = useRef<number | null>(null);
  const filterChangedSinceOpen =
    sessionId !== null &&
    sessionOpenedAtVersionRef.current !== null &&
    filterVersion !== sessionOpenedAtVersionRef.current;
  const totalDrifted =
    scope !== null && currentTotal !== null && currentTotal !== scope.openedTotal;
  const scopeStale = sessionId !== null && (filterChangedSinceOpen || totalDrifted);

  // Keyed on the count, so an in-bubble update doesn't snap the scroll mid-read.
  // biome-ignore lint/correctness/useExhaustiveDependencies: trigger only
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [history.length]);

  const send = async () => {
    const text = input.trim();
    if (!text || pending || !ready) return;
    setInput("");
    const userMsg: Message = { id: ++idRef.current, role: "user", text };
    const pendingMsg: Message = {
      id: ++idRef.current,
      role: "assistant",
      text: "",
      pending: true,
    };
    setHistory((prev) => [...prev, userMsg, pendingMsg]);
    setPending(true);
    try {
      if (!sessionId) {
        const resp = await openChat(crisisId, { filter, message: text });
        setSessionId(resp.session_id);
        setScope({
          size: resp.k_size ?? 0,
          openedTotal: resp.opened_total ?? 0,
          signature: resp.filter_signature ?? "",
        });
        // The K-set is locked at open, so this holds across follow-up turns.
        onContextChange?.(resp.context_reports ?? []);
        sessionOpenedAtVersionRef.current = filterVersion;
        setHistory((prev) =>
          prev.map((m) =>
            m.id === pendingMsg.id
              ? {
                  ...m,
                  text: resp.text,
                  citations: resp.cited_report_ids,
                  answeredAt: resp.answered_at,
                  pending: false,
                }
              : m,
          ),
        );
      } else {
        const resp = await chatTurn(crisisId, { session_id: sessionId, message: text });
        setHistory((prev) =>
          prev.map((m) =>
            m.id === pendingMsg.id
              ? {
                  ...m,
                  text: resp.text,
                  citations: resp.cited_report_ids,
                  answeredAt: resp.answered_at,
                  pending: false,
                }
              : m,
          ),
        );
      }
    } catch (e) {
      setHistory((prev) =>
        prev.map((m) =>
          m.id === pendingMsg.id
            ? {
                ...m,
                text: e instanceof Error ? e.message : String(e),
                pending: false,
              }
            : m,
        ),
      );
    } finally {
      setPending(false);
    }
  };

  // The fresh session opens on the next message.
  const reset = () => {
    setSessionId(null);
    setScope(null);
    sessionOpenedAtVersionRef.current = null;
    setHistory([]);
    onContextChange?.([]);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        padding: "0 0 0 0",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "12px 18px 8px",
          borderBottom: "1px solid var(--c-line-2)",
        }}
      >
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 0.06,
            textTransform: "uppercase",
            color: "var(--c-ink-3)",
            flex: 1,
          }}
        >
          Chat with the filtered set
        </span>
        {(sessionId || history.length > 0) && (
          <button
            type="button"
            className="btn secondary"
            onClick={reset}
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            New chat
          </button>
        )}
      </header>

      {scope && (
        <div
          style={{
            padding: "6px 18px",
            fontSize: 11,
            color: "var(--c-ink-3)",
            background: "var(--c-blue-50)",
            borderBottom: "1px solid var(--c-line-2)",
          }}
        >
          {scope.size > 0 ? (
            <>
              scoped to {scope.size} sampled reports of {scope.openedTotal.toLocaleString()}{" "}
              matching
            </>
          ) : (
            <>
              answering from aggregate stats over {scope.openedTotal.toLocaleString()} matching
              reports (too many to sample individually)
            </>
          )}{" "}
          · signature <code style={{ fontSize: 10 }}>{scope.signature.slice(0, 8)}</code>
        </div>
      )}

      {scopeStale && scope && (
        <output
          aria-live="polite"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            margin: "10px 18px 0",
            padding: "10px 12px",
            background: "var(--c-warn-bg)",
            color: "var(--c-warn)",
            borderRadius: 8,
            fontSize: 12,
            border: "1px solid var(--c-warn)",
          }}
        >
          <span style={{ flex: 1, lineHeight: 1.4 }}>
            This chat was opened against <strong>{scope.openedTotal.toLocaleString()}</strong>{" "}
            reports
            {currentTotal !== null && currentTotal !== scope.openedTotal && (
              <>
                ; the current filter shows <strong>{currentTotal.toLocaleString()}</strong>
              </>
            )}
            . Start a new chat to use the latest scope.
          </span>
          <button
            type="button"
            className="btn primary"
            onClick={reset}
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            New chat
          </button>
        </output>
      )}

      <div
        ref={scrollRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: "12px 18px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {!ready && history.length === 0 && (
          <EmptyHint
            title="Run a search to start a chat"
            body="Chat answers from the reports your current filter selects, using both the view's stats and the reports themselves. Set the filters and map to the reports you want to ask about, then run a search."
          />
        )}
        {ready && history.length === 0 && (
          <EmptyHint
            title="Ask about this view"
            body="Chat answers from the reports your current filter selects: it has the view's stats for counts and breakdowns, and pulls the most relevant reports to quote and cite. Examples: 'What's the breakdown by damage severity?', 'What are people reporting about blocked roads?', 'Which infrastructure type is most affected?'"
          />
        )}
        {history.map((m) => (
          <Bubble key={m.id} message={m} onCiteFocus={onCiteFocus} />
        ))}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        style={{
          display: "flex",
          gap: 8,
          padding: "10px 18px 12px",
          borderTop: "1px solid var(--c-line-2)",
          background: "var(--c-surface)",
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            ready
              ? sessionId
                ? "Ask a follow-up…"
                : "Try: 'what's the breakdown by damage severity?'"
              : "Run a search first"
          }
          disabled={!ready || pending}
          style={{
            flex: 1,
            padding: "8px 12px",
            fontSize: 13,
            border: "1px solid var(--c-line)",
            borderRadius: 8,
            background: "var(--c-card)",
            color: "var(--c-ink)",
            fontFamily: "inherit",
            outline: "none",
          }}
          onFocusCapture={(e) => {
            e.currentTarget.style.borderColor = "var(--c-blue-600)";
            e.currentTarget.style.boxShadow = "0 0 0 3px var(--c-blue-100)";
          }}
          onBlurCapture={(e) => {
            e.currentTarget.style.borderColor = "var(--c-line)";
            e.currentTarget.style.boxShadow = "none";
          }}
        />
        <button
          type="submit"
          className={`btn primary${pending ? " send-shimmer" : ""}`}
          disabled={!ready || pending || !input.trim()}
          style={{ padding: "8px 16px", fontSize: 13 }}
        >
          {pending ? "Sending…" : "Send"}
        </button>
      </form>
    </div>
  );
}

// Inline citation marker; validated server-side against the locked K-set.
const CITE_RE = /\[report_id=([0-9a-fA-F-]{36})\]/g;

function Bubble({
  message,
  onCiteFocus,
}: { message: Message; onCiteFocus?: (reportId: string) => void }) {
  const user = message.role === "user";
  // 1-based, in server order, shared by inline chips and the Sources footer.
  const citeNumber = new Map<string, number>();
  for (const id of message.citations ?? []) {
    if (!citeNumber.has(id)) citeNumber.set(id, citeNumber.size + 1);
  }
  return (
    <div
      className="fade-in"
      style={{
        alignSelf: user ? "flex-end" : "flex-start",
        maxWidth: "88%",
        padding: "8px 12px",
        borderRadius: 12,
        background: user ? "var(--c-blue-700)" : "var(--c-card)",
        color: user ? "#fff" : "var(--c-ink)",
        border: user ? "1px solid var(--c-blue-700)" : "1px solid var(--c-line)",
        fontSize: 13,
        lineHeight: 1.45,
        whiteSpace: "pre-wrap",
        boxShadow: "var(--shadow-1)",
      }}
    >
      {message.pending ? (
        <span
          className="dots"
          aria-label="Assistant is thinking"
          style={{ color: "var(--c-ink-3)" }}
        >
          <span /> <span /> <span />
        </span>
      ) : (
        <>
          <div>
            <AnswerText text={message.text} citeNumber={citeNumber} onCiteFocus={onCiteFocus} />
          </div>
          {message.citations && message.citations.length > 0 && (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                gap: 6,
                marginTop: 8,
                paddingTop: 7,
                borderTop: "1px solid var(--c-line-2)",
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: 0.04,
                  textTransform: "uppercase",
                  color: "var(--c-ink-3)",
                }}
              >
                Sources
              </span>
              {message.citations.map((id) => (
                <CiteChip
                  key={id}
                  n={citeNumber.get(id) ?? 0}
                  reportId={id}
                  onCiteFocus={onCiteFocus}
                  variant="footer"
                />
              ))}
            </div>
          )}
          {!user && message.answeredAt && (
            <div
              style={{
                fontSize: 10,
                marginTop: 4,
                color: "var(--c-ink-3)",
                fontStyle: "italic",
              }}
            >
              scope from {formatScopeStamp(message.answeredAt)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// Swaps each `[report_id=<uuid>]` marker for a numbered chip.
function AnswerText({
  text,
  citeNumber,
  onCiteFocus,
}: {
  text: string;
  citeNumber: Map<string, number>;
  onCiteFocus?: (reportId: string) => void;
}) {
  const parts: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(CITE_RE)) {
    const start = m.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    const id = m[1];
    parts.push(
      <CiteChip
        key={`c${key++}`}
        n={citeNumber.get(id) ?? 0}
        reportId={id}
        onCiteFocus={onCiteFocus}
        variant="inline"
      />,
    );
    last = start + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

// `inline` sits in the answer text, `footer` in the Sources row.
function CiteChip({
  n,
  reportId,
  onCiteFocus,
  variant,
}: {
  n: number;
  reportId: string;
  onCiteFocus?: (reportId: string) => void;
  variant: "inline" | "footer";
}) {
  const inline = variant === "inline";
  return (
    <button
      type="button"
      onClick={() => onCiteFocus?.(reportId)}
      disabled={!onCiteFocus}
      title="Show this report on the map"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: inline ? 16 : 20,
        height: inline ? 16 : 20,
        padding: inline ? "0 4px" : "0 6px",
        margin: inline ? "0 1px" : 0,
        verticalAlign: inline ? "text-top" : "baseline",
        fontSize: inline ? 10 : 11,
        fontWeight: 700,
        lineHeight: 1,
        borderRadius: 6,
        border: "1px solid var(--c-blue-200, #bfdbfe)",
        background: "var(--c-blue-50)",
        color: "var(--c-blue-700)",
        cursor: onCiteFocus ? "pointer" : "default",
      }}
    >
      {n || "·"}
    </button>
  );
}

function EmptyHint({ title, body }: { title: string; body: string }) {
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
        ?
      </span>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-ink)" }}>{title}</div>
      <div style={{ fontSize: 12, color: "var(--c-ink-3)", maxWidth: 320, lineHeight: 1.4 }}>
        {body}
      </div>
    </div>
  );
}
