import { useEffect, useMemo, useRef, useState } from "react";
import { fetchCountries } from "../../api/admin";
import type { CountryRow } from "../../types/admin";

interface Props {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}

// Combo-box multiselect: chips and input share one field; type to filter, click
// or Enter to add, Backspace on an empty input removes the last chip.
export function CountriesMultiselect({ value, onChange, placeholder }: Props) {
  const [countries, setCountries] = useState<CountryRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchCountries()
      .then((rows) => {
        if (!cancelled) setCountries(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const byIso2 = useMemo(() => {
    const map = new Map<string, CountryRow>();
    for (const c of countries) map.set(c.iso2, c);
    return map;
  }, [countries]);

  // Prefix-match on name or ISO-2, hiding already-selected rows.
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const remaining = countries.filter((c) => !value.includes(c.iso2));
    if (!needle) return remaining;
    return remaining.filter(
      (c) => c.name.toLowerCase().includes(needle) || c.iso2.toLowerCase().startsWith(needle),
    );
  }, [countries, q, value]);

  useEffect(() => {
    // Keep activeIndex in range as the filter shrinks/grows.
    setActiveIndex((i) => Math.min(Math.max(0, i), Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  const add = (iso2: string) => {
    if (!value.includes(iso2)) onChange([...value, iso2]);
    setQ("");
    setActiveIndex(0);
    inputRef.current?.focus();
  };

  const remove = (iso2: string) => onChange(value.filter((v) => v !== iso2));

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      setOpen(true);
      setActiveIndex((i) => Math.min(filtered.length - 1, i + 1));
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      setActiveIndex((i) => Math.max(0, i - 1));
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (filtered.length > 0) {
        add(filtered[activeIndex].iso2);
        e.preventDefault();
      }
    } else if (e.key === "Backspace" && q.length === 0 && value.length > 0) {
      remove(value[value.length - 1]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <div
        onClick={() => {
          inputRef.current?.focus();
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.focus();
        }}
        style={{
          width: "100%",
          minHeight: 46,
          padding: "8px 10px",
          border: "1px solid var(--c-line)",
          borderRadius: 8,
          background: "var(--c-card)",
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          alignItems: "center",
          cursor: "text",
        }}
      >
        {value.map((iso2) => {
          const c = byIso2.get(iso2);
          return (
            <span
              key={iso2}
              className="chip blue"
              style={{
                padding: "4px 10px",
                fontSize: 14,
                display: "inline-flex",
                gap: 6,
                alignItems: "center",
              }}
            >
              <span>{c ? c.name : iso2}</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  remove(iso2);
                }}
                style={{
                  cursor: "pointer",
                  color: "var(--c-ink-3)",
                  background: "transparent",
                  border: "none",
                  padding: 0,
                  font: "inherit",
                  lineHeight: 1,
                }}
                aria-label={`Remove ${c ? c.name : iso2}`}
              >
                ×
              </button>
            </span>
          );
        })}
        <input
          ref={inputRef}
          type="text"
          value={q}
          placeholder={value.length === 0 ? (placeholder ?? "Type a country name…") : ""}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          style={{
            flex: value.length === 0 ? 1 : "1 0 120px",
            minWidth: 80,
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--c-ink)",
            fontSize: 15,
            padding: "4px 6px",
            fontFamily: "inherit",
          }}
        />
      </div>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            insetInline: 0,
            background: "var(--c-card)",
            border: "1px solid var(--c-line)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.18)",
            zIndex: 20,
            maxHeight: 280,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ overflowY: "auto", flex: 1 }}>
            {error && (
              <div style={{ padding: 12, fontSize: 14, color: "var(--c-danger)" }}>{error}</div>
            )}
            {!error && filtered.length === 0 && (
              <div style={{ padding: 12, fontSize: 14, color: "var(--c-ink-3)" }}>
                {q.trim() ? "No matches." : "All countries already selected."}
              </div>
            )}
            {filtered.map((c, i) => {
              const active = i === activeIndex;
              return (
                <button
                  key={c.iso2}
                  type="button"
                  onMouseEnter={() => setActiveIndex(i)}
                  onClick={() => add(c.iso2)}
                  aria-pressed={active}
                  style={{
                    width: "100%",
                    textAlign: "start",
                    padding: "9px 12px",
                    background: active ? "var(--c-blue-50)" : "transparent",
                    border: "none",
                    cursor: "pointer",
                    fontSize: 15,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    color: "var(--c-ink)",
                  }}
                >
                  <span style={{ flex: 1 }}>{c.name}</span>
                  <span className="mono" style={{ fontSize: 13, color: "var(--c-ink-3)" }}>
                    {c.iso2}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
