import { getAccessEmail } from "../../api/auth";
import { useTheme } from "../../hooks/useTheme";
import { TimezonePicker } from "./TimezonePicker";

export type AdminTab = "dashboard" | "analysis" | "crises" | "admins";

// Avatar initials from the email's local part: "dash-preview@…" → "DP",
// "azguir@…" → "AZ"; "??" without an email.
function initialsFromEmail(email: string | null): string {
  if (!email) return "??";
  const local = email.split("@")[0] ?? "";
  const parts = local.split(/[.\-_+]+/).filter(Boolean);
  const letters =
    parts.length >= 2 ? parts[0][0] + parts[1][0] : local.replace(/[^a-z0-9]/gi, "").slice(0, 2);
  return (letters || "??").toUpperCase();
}

interface Props {
  active: AdminTab;
  onNavigate: (tab: AdminTab) => void;
  onLogout?: () => void;
  /** Replays the first-run guided walkthrough from the welcome splash. */
  onStartWalkthrough?: () => void;
}

// `analysis` is deliberately not a top-nav tab: it is a single-crisis
// drill-down reached from the dashboard inspector, so it lights up Dashboard.
const TABS: { id: AdminTab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "crises", label: "Crises" },
  { id: "admins", label: "Admins" },
];

function highlightFor(active: AdminTab): AdminTab {
  return active === "analysis" ? "dashboard" : active;
}

export function AdminTopbar({ active, onNavigate, onLogout, onStartWalkthrough }: Props) {
  const { theme, toggle: toggleTheme } = useTheme();
  const highlight = highlightFor(active);
  const email = getAccessEmail();
  const initials = initialsFromEmail(email);
  return (
    <div
      style={{
        height: 52,
        background: "var(--c-blue-900)",
        color: "#fff",
        display: "flex",
        alignItems: "center",
        padding: "0 18px",
        gap: 16,
        flexShrink: 0,
        position: "relative",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 10,
          fontSize: 14,
          letterSpacing: "-0.005em",
        }}
      >
        <img
          src="/favicon-admin.svg"
          alt="RASID Admin"
          width={34}
          height={34}
          style={{ borderRadius: 8, boxShadow: "0 0 0 1px rgba(255,255,255,0.18)" }}
        />
      </span>

      <span className="topbar-divider" aria-hidden="true" />

      <nav aria-label="Admin sections" style={{ display: "flex", gap: 2 }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-tour={`nav-${t.id}`}
            className={`admin-tab${highlight === t.id ? " active" : ""}`}
            aria-current={highlight === t.id ? "page" : undefined}
            onClick={() => onNavigate(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div style={{ flex: 1 }} />

      {onStartWalkthrough && (
        <button
          type="button"
          onClick={onStartWalkthrough}
          aria-label="Replay the guided walkthrough"
          title="Replay the guided walkthrough"
          className="topbar-btn"
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
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 .9-1 1.7" />
            <path d="M12 17h.01" />
          </svg>
          <span>Walkthrough</span>
        </button>
      )}

      <button
        type="button"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        className="topbar-btn"
      >
        <span aria-hidden="true" style={{ fontSize: 13, lineHeight: 1 }}>
          {theme === "dark" ? "☀" : "☾"}
        </span>
        <span>{theme === "dark" ? "Light" : "Dark"}</span>
      </button>

      <TimezonePicker />

      {onLogout && (
        <>
          <span className="topbar-divider" aria-hidden="true" />
          <button
            type="button"
            onClick={onLogout}
            aria-label="Sign out"
            className="topbar-btn is-ghost"
          >
            Sign out
          </button>
        </>
      )}

      <span
        className="topbar-avatar"
        aria-label={email ? `Signed in as ${email}` : "Account"}
        title={email ?? undefined}
      >
        {initials}
      </span>
    </div>
  );
}
