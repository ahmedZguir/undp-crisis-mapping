/** Generic debounced async autocomplete; callers supply the fetcher and row renderer. */
import { useEffect, useRef, useState } from "react";

interface Props<T> {
  placeholder: string;
  ariaLabel: string;
  // Should return [] for an empty query.
  search: (q: string, signal: AbortSignal) => Promise<T[]>;
  onPick: (hit: T) => void;
  renderItem: (hit: T) => React.ReactNode;
  keyFor: (hit: T) => string;
  // Rendered inside the input.
  helperText?: string;
}

export function Autocomplete<T>({
  placeholder,
  ariaLabel,
  search,
  onPick,
  renderItem,
  keyFor,
  helperText,
}: Props<T>) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState<T[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Latest wins: an older fetch resolving late must not overwrite the dropdown.
  const requestIdRef = useRef(0);

  useEffect(() => {
    const trimmed = q.trim();
    if (trimmed.length < 1) {
      setHits([]);
      setLoading(false);
      setError(null);
      abortRef.current?.abort();
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    requestIdRef.current += 1;
    const myId = requestIdRef.current;
    setError(null);
    // Show the skeleton only after 150ms in flight; previous hits stay until new ones arrive.
    const loadingTimer = window.setTimeout(() => setLoading(true), 150);
    const fireTimer = window.setTimeout(() => {
      search(trimmed, ctrl.signal)
        .then((rows) => {
          if (ctrl.signal.aborted || myId !== requestIdRef.current) return;
          window.clearTimeout(loadingTimer);
          setHits(rows);
          setLoading(false);
        })
        .catch((e) => {
          if (ctrl.signal.aborted || myId !== requestIdRef.current) return;
          window.clearTimeout(loadingTimer);
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        });
    }, 120);
    return () => {
      window.clearTimeout(fireTimer);
      window.clearTimeout(loadingTimer);
    };
  }, [q, search]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={containerRef} style={{ position: "relative" }}>
      <input
        type="text"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        style={{
          width: "100%",
          padding: "8px 10px",
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
      {open && (q.trim().length > 0 || loading) && (
        // Deliberately not role="listbox": that implies arrow-key navigation we don't implement.
        // Rows are buttons; Enter in the input picks the first hit.
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            background: "#fff",
            border: "1px solid var(--c-line)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(20,24,36,0.18)",
            zIndex: 50,
            maxHeight: 280,
            overflowY: "auto",
          }}
        >
          {loading && hits.length === 0 && (
            <div style={{ padding: 6 }}>
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="skeleton"
                  style={{ height: 28, margin: "4px 6px", borderRadius: 6 }}
                />
              ))}
            </div>
          )}
          {!loading && error && (
            <div
              style={{
                padding: "10px 12px",
                fontSize: 12,
                color: "var(--c-danger)",
              }}
            >
              {error}
            </div>
          )}
          {!loading && !error && hits.length === 0 && q.trim().length > 0 && (
            <div
              style={{
                padding: "10px 12px",
                fontSize: 12,
                color: "var(--c-ink-3)",
              }}
            >
              No matches.
            </div>
          )}
          {!error &&
            hits.map((hit) => (
              <button
                key={keyFor(hit)}
                type="button"
                onClick={() => {
                  onPick(hit);
                  setQ("");
                  setOpen(false);
                  setHits([]);
                }}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "start",
                  background: "transparent",
                  border: "none",
                  padding: "8px 10px",
                  cursor: "pointer",
                  font: "inherit",
                  color: "var(--c-ink)",
                  borderBottom: "1px solid var(--c-line-2)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--c-blue-50)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {renderItem(hit)}
              </button>
            ))}
        </div>
      )}
      {helperText && (
        <div
          style={{
            fontSize: 10,
            color: "var(--c-ink-3)",
            marginTop: 3,
          }}
        >
          {helperText}
        </div>
      )}
    </div>
  );
}
