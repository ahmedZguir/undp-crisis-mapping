import { useEffect, useRef, useState } from "react";
import type { AdminCrisis } from "../../../types/admin";

// The dashboard's onboarding surface. With no crisis selected it shows
// "Getting started" (create, activate) ticked off from real data; with a crisis
// selected, "Explore the dashboard" with the guided tour and hands-on tasks.
// Once Explore is complete it retires for good via `onDashboardExplored`
// (persisted per account by AdminApp, so it holds across devices).

interface Props {
  crises: AdminCrisis[];
  currentCrisis: AdminCrisis | null;
  selectedCrisisId: string | null;
  /** Whole-crisis report total for the selected crisis (baseline stats). */
  reportCount: number | null;
  // Live dashboard signals used to tick off the "things to try" tasks. Each is
  // latched once seen, so a task stays done even after the user moves on.
  queryActive: boolean;
  view: "map" | "list";
  selectedReportId: string | null;
  // Whether the coordinator has opened the AI Summary / Analysis advanced tools.
  // These arrive already latched (monotonic) from ReportsPage.
  summaryViewed: boolean;
  analysisViewed: boolean;
  // Per-account retirement, persisted server-side by AdminApp.
  dashboardExplored: boolean;
  onDashboardExplored?: () => void;
}

interface StepItem {
  key: string;
  label: string;
  hint: string;
  done: boolean;
  optional?: boolean;
  action?: { label: string; onClick: () => void };
}

function CheckDot({ done }: { done: boolean }) {
  return (
    <span className={`ob-check${done ? " done" : ""}`} aria-hidden="true">
      {done && (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M5 13l4 4L19 7" />
        </svg>
      )}
    </span>
  );
}

const goCrises = () => {
  window.location.hash = "#/crises";
};

export function OnboardingPanel({
  crises,
  currentCrisis,
  selectedCrisisId,
  reportCount,
  queryActive,
  view,
  selectedReportId,
  summaryViewed,
  analysisViewed,
  dashboardExplored,
  onDashboardExplored,
}: Props) {
  // Start collapsed when we mount already in explore mode (a crisis is
  // selected) so the panel doesn't crowd it; the effect below handles the
  // setup → explore transition mid-session.
  const [collapsed, setCollapsed] = useState(() => selectedCrisisId != null);
  // Latched "things to try" completions. Once a task is satisfied it stays
  // ticked for the rest of the session.
  const [seen, setSeen] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const next: string[] = [];
    if (queryActive) next.push("search");
    if (view === "list") next.push("list");
    if (selectedReportId) next.push("report");
    if (next.length === 0) return;
    setSeen((prev) => {
      const has = next.every((k) => prev.has(k));
      if (has) return prev;
      const merged = new Set(prev);
      for (const k of next) merged.add(k);
      return merged;
    });
  }, [queryActive, view, selectedReportId]);

  const mode: "setup" | "explore" = selectedCrisisId == null ? "setup" : "explore";

  // Collapse once on first entering explore mode so it doesn't crowd the
  // crisis; a later manual expand isn't undone. Setup mode stays expanded.
  const exploreCollapsedRef = useRef(mode === "explore");
  useEffect(() => {
    if (mode === "explore" && !exploreCollapsedRef.current) {
      exploreCollapsedRef.current = true;
      setCollapsed(true);
    }
  }, [mode]);

  let title: string;
  let subtitle: string;
  let items: StepItem[];

  if (mode === "setup") {
    const hasCrisis = crises.length > 0;
    const ingested = crises.some((c) => (c.buildings_ingested_count ?? 0) > 0);
    const activated = crises.some((c) => c.status === "active");
    const hasReports = (reportCount ?? 0) > 0;
    title = "Getting started";
    subtitle =
      "Set up a crisis so residents can start reporting damage. Each step ticks off as you complete it.";
    items = [
      {
        key: "create",
        label: "Create your first crisis",
        hint: "Name it, set its area, and load the building footprints.",
        done: hasCrisis,
        action: hasCrisis ? undefined : { label: "Create a crisis", onClick: goCrises },
      },
      {
        key: "form",
        label: "Customize the report form",
        hint: "Optional. Tailor the questions residents answer for this crisis.",
        done: false,
        optional: true,
        action: { label: "Open Crises", onClick: goCrises },
      },
      {
        key: "ingest",
        label: "Load building footprints",
        hint: "So each report maps onto a real building.",
        done: ingested,
      },
      {
        key: "activate",
        label: "Activate the crisis",
        hint: "New crises stay inactive until you turn them on. Only then can residents report.",
        done: activated,
        action: activated || !hasCrisis ? undefined : { label: "Open Crises", onClick: goCrises },
      },
      {
        key: "reports",
        label: "Watch reports arrive",
        hint: "Pick the crisis above. Reports show up here as residents submit them.",
        done: hasReports,
      },
    ];
  } else {
    const name = currentCrisis?.name ?? "This crisis";
    const n = reportCount ?? 0;
    title = "Try it yourself";
    subtitle =
      n > 0
        ? `${name} has ${n.toLocaleString()} report${n === 1 ? "" : "s"}. Here's how to read it.`
        : `${name} is selected. Here's how to drive the dashboard.`;
    items = [
      {
        key: "search",
        label: "Search by meaning",
        hint: "Try the search box: “water supply”, “blocked road”, “collapsed roof”.",
        done: seen.has("search"),
      },
      {
        key: "list",
        label: "Switch to the list view",
        hint: "See every matching report as a sortable table.",
        done: seen.has("list"),
      },
      {
        key: "report",
        label: "Open a report",
        hint: "Click any point or row to inspect its photo, AI summary, and detail.",
        done: seen.has("report"),
      },
      {
        key: "summary",
        label: "Generate an AI summary",
        hint: "An AI overview of the reports in your current view: the themes, where, and how severe.",
        done: summaryViewed,
      },
      {
        key: "analysis",
        label: "View live analysis",
        hint: "Priority areas and blind spots, computed live over the crisis's reports.",
        done: analysisViewed,
      },
    ];
  }

  const tracked = items.filter((it) => !it.optional);
  const doneCount = tracked.filter((it) => it.done).length;
  const allDone = doneCount === tracked.length;

  // Retired in both modes: finishing Explore implies an active crisis exists.
  if (dashboardExplored) return null;

  // When every Explore task is done, show an explicit completion card rather
  // than vanishing under a click. Its Done button flips the AdminApp flag.
  if (mode === "explore" && allDone) {
    const name = currentCrisis?.name;
    return (
      <section className="ob-panel is-explore is-done" aria-label="You're all set">
        <header className="ob-head">
          <span className="ob-spark is-done" aria-hidden="true">
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 13l4 4L19 7" />
            </svg>
          </span>
          <div className="ob-headtext">
            <h2>You're all set</h2>
            <p>
              You've explored the dashboard{name ? ` for ${name}` : ""} and you're ready to
              coordinate the response. Replay the walkthrough anytime from Walkthrough in the top
              bar.
            </p>
          </div>
          <button type="button" className="btn ob-done-btn" onClick={() => onDashboardExplored?.()}>
            Got it
          </button>
        </header>
      </section>
    );
  }

  return (
    <section
      className={`ob-panel is-${mode}${collapsed ? " is-collapsed" : ""}`}
      aria-label={title}
    >
      <header className="ob-head">
        <span className="ob-spark" aria-hidden="true">
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
            <circle cx="12" cy="12" r="3.2" />
          </svg>
        </span>
        <div className="ob-headtext">
          <h2>{title}</h2>
          {!collapsed && <p>{subtitle}</p>}
        </div>
        <span className={`ob-progress${allDone ? " done" : ""}`}>
          {allDone ? "All tasks done" : `${doneCount} of ${tracked.length} tasks done`}
        </span>
        <button
          type="button"
          className="ob-collapse"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand getting started" : "Collapse getting started"}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d={collapsed ? "M6 9l6 6 6-6" : "M6 15l6-6 6 6"} />
          </svg>
        </button>
      </header>

      {!collapsed && (
        <ol className="ob-list">
          {items.map((it) => (
            <li
              key={it.key}
              className={`ob-item${it.done ? " done" : ""}${it.optional ? " optional" : ""}`}
            >
              <CheckDot done={it.done} />
              <div className="ob-text">
                <span className="ob-label">
                  {it.label}
                  {it.optional && <span className="ob-tag">optional</span>}
                </span>
                <span className="ob-hint">{it.hint}</span>
              </div>
              {it.action && !it.done && (
                <button type="button" className="btn secondary ob-act" onClick={it.action.onClick}>
                  {it.action.label}
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
