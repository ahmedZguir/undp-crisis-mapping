import { formatInTimeZone } from "date-fns-tz";
import { useEffect, useRef, useState } from "react";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import {
  COMMON_TIMEZONES,
  getBrowserTimezone,
  getStoredTimezone,
  setStoredTimezone,
  timezoneOptionLabel,
} from "../../lib/adminTimezone";

/** Live HH:mm in the effective timezone, refreshed every 30 seconds. */
function useNowInTimezone(tz: string): string {
  const [now, setNow] = useState(() => formatInTimeZone(new Date(), tz, "HH:mm"));
  useEffect(() => {
    const tick = () => setNow(formatInTimeZone(new Date(), tz, "HH:mm"));
    tick();
    const id = window.setInterval(tick, 30_000);
    return () => window.clearInterval(id);
  }, [tz]);
  return now;
}

/** Topbar TZ pill: shows the effective TZ (override or browser) and opens a
 *  dropdown to switch it. The override persists via `lib/adminTimezone.ts`. */
export function TimezonePicker() {
  const effective = useAdminTimezone();
  const browser = getBrowserTimezone();
  const override = getStoredTimezone();
  const nowLabel = useNowInTimezone(effective);

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const options = uniqueTimezones([browser, effective, ...COMMON_TIMEZONES]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={
          override
            ? `Display timezone: ${effective} (override; browser is ${browser})`
            : `Display timezone: ${effective} (browser default)`
        }
        style={{
          background: "transparent",
          border: "1px solid rgba(255,255,255,0.3)",
          color: "#fff",
          padding: "4px 10px",
          borderRadius: 4,
          fontSize: 11,
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <svg
          aria-hidden="true"
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ flexShrink: 0, opacity: 0.85 }}
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
        <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{effective}</span>
        <span
          aria-label="Current time"
          style={{
            fontFamily: "var(--font-mono, monospace)",
            fontWeight: 700,
            letterSpacing: 0.4,
          }}
        >
          {nowLabel}
        </span>
        {override && (
          <span
            aria-hidden="true"
            title="Manual override"
            style={{
              fontSize: 9,
              fontWeight: 700,
              padding: "1px 5px",
              borderRadius: 99,
              background: "rgba(255,255,255,0.18)",
              letterSpacing: 0.4,
            }}
          >
            SET
          </span>
        )}
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            minWidth: 260,
            maxHeight: 360,
            overflowY: "auto",
            background: "#fff",
            color: "#222",
            border: "1px solid var(--c-line)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.18)",
            padding: 6,
            zIndex: 1000,
            fontSize: 12,
          }}
        >
          <div
            style={{
              padding: "6px 10px",
              fontSize: 11,
              color: "var(--c-ink-3)",
              textTransform: "uppercase",
              letterSpacing: 0.5,
            }}
          >
            Display timezone
          </div>
          <button
            type="button"
            onClick={() => {
              setStoredTimezone(null);
              setOpen(false);
            }}
            style={menuItemStyle(override === null)}
          >
            <span>Use browser default</span>
            <span style={{ color: "var(--c-ink-3)", marginInlineStart: 8 }}>{browser}</span>
          </button>
          <div style={{ height: 1, background: "var(--c-line)", margin: "6px 0" }} />
          {options.map((tz) => (
            <button
              key={tz}
              type="button"
              onClick={() => {
                setStoredTimezone(tz);
                setOpen(false);
              }}
              style={menuItemStyle(override === tz)}
            >
              <span>{tz}</span>
              <span style={{ color: "var(--c-ink-3)", marginInlineStart: 8 }}>
                {tzOffsetSuffix(tz)}
              </span>
            </button>
          ))}
          <div style={{ height: 1, background: "var(--c-line)", margin: "6px 0" }} />
          <CustomTimezoneEntry
            onSubmit={(tz) => {
              setStoredTimezone(tz);
              setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}

function CustomTimezoneEntry({ onSubmit }: { onSubmit: (tz: string) => void }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const tryCommit = () => {
    const tz = text.trim();
    if (!tz) return;
    try {
      // The runtime rejects invalid IANA names with a RangeError.
      new Intl.DateTimeFormat(undefined, { timeZone: tz });
      onSubmit(tz);
      setText("");
      setErr(null);
    } catch {
      setErr(`"${tz}" isn't a valid IANA timezone.`);
    }
  };

  return (
    <div style={{ padding: "4px 10px 6px" }}>
      <div style={{ fontSize: 11, color: "var(--c-ink-3)", marginBottom: 4 }}>
        Or type any IANA timezone
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <input
          type="text"
          value={text}
          placeholder="e.g. Africa/Lagos"
          onChange={(e) => {
            setText(e.target.value);
            if (err) setErr(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              tryCommit();
            }
          }}
          style={{
            flex: 1,
            minWidth: 0,
            padding: "4px 6px",
            border: "1px solid var(--c-line)",
            borderRadius: 4,
            fontSize: 12,
          }}
        />
        <button
          type="button"
          onClick={tryCommit}
          disabled={text.trim() === ""}
          style={{
            padding: "4px 10px",
            fontSize: 12,
            border: "1px solid var(--c-line)",
            borderRadius: 4,
            background: "var(--c-card)",
            cursor: text.trim() === "" ? "not-allowed" : "pointer",
          }}
        >
          Set
        </button>
      </div>
      {err && <div style={{ fontSize: 11, color: "var(--c-danger)", marginTop: 4 }}>{err}</div>}
    </div>
  );
}

function uniqueTimezones(list: string[]): string[] {
  return Array.from(new Set(list.filter(Boolean)));
}

function tzOffsetSuffix(tz: string): string {
  try {
    return timezoneOptionLabel(tz).split(" ").slice(-1)[0] ?? "";
  } catch {
    return "";
  }
}

function menuItemStyle(active: boolean): React.CSSProperties {
  return {
    display: "flex",
    width: "100%",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "6px 10px",
    border: "none",
    background: active ? "var(--c-blue-50)" : "transparent",
    color: "inherit",
    textAlign: "left",
    fontSize: 12,
    fontWeight: active ? 700 : 400,
    cursor: "pointer",
    borderRadius: 4,
  };
}
