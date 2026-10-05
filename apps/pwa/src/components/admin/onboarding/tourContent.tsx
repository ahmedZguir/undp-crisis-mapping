import type { TourStep } from "./GuidedTour";

// Step content for the two guided chapters. Anchors (`target`) match
// `data-tour` attributes on the topbar nav, CrisesPage (chapter 1) and
// ReportsPage / AnalyticsPanel (chapter 2).

// The page actions chapter 1 drives. Selecting an existing crisis reveals the
// lifecycle controls so we can point at the real Activate button; clearing it
// returns to the create form.
export interface CrisesActions {
  /** Show the create form (deselect any crisis). */
  clearSelection: () => void;
  /** Open an existing (preferably inactive) crisis so the detail view and its
   *  Activate button show. Left selected across the detail steps; the earlier
   *  "inactive" step clears it on entry when stepping back. */
  selectForActivation: () => void;
  /** Name of the inactive crisis the tour opens, so the step can name it. Null
   *  until crises have loaded. */
  demoName?: string | null;
}

// Chapter 1: the Crises tab, the create form, then an inactive crisis and its
// sections in on-screen order. Nothing is filled or submitted, so the
// walkthrough never leaves a half-made crisis.
export function buildCrisesSteps(actions: CrisesActions): TourStep[] {
  return [
    {
      title: "The Crises tab",
      body: "A crisis is the event people report against: a flood, a quake, a strike. Reporting opens only once one is live. This tab is where you create and manage them.",
      target: "nav-crises",
      onEnter: () => {
        actions.clearSelection();
        return undefined;
      },
    },
    {
      title: "Create a crisis here",
      body: "This is the whole create form. Start at the top with a name and type, then work down through the affected area, what the public sees, building shapes, and the report form. Don't worry about getting each field right now: every one stays editable later.",
      target: "setup-fields",
    },
    {
      title: "Then create it",
      body: "This saves the crisis as inactive: stored and ready, but not yet visible to citizens. Let's look at what happens next.",
      target: "setup-create",
    },
    {
      title: "Your crises list",
      body: "Every crisis lives here, grouped by state: active (live to citizens), inactive (saved but hidden), and archived.",
      target: "setup-rail",
    },
    {
      title: "Activate a crisis",
      body: actions.demoName
        ? `${actions.demoName} is inactive: saved but hidden from citizens. Let's open it and make it live.`
        : "This crisis is inactive: saved but hidden from citizens. Let's open it and make it live.",
      target: "setup-inactive",
      // Deselect on (re)entry so stepping back from the detail steps returns to
      // the list view, where this anchor in the rail is the one being pointed at.
      onEnter: () => {
        actions.clearSelection();
        return undefined;
      },
    },
    {
      title: "Review the settings",
      body: "Opening a crisis shows everything about it. Section 1 holds its core settings: name, type, area, and what the public sees. Edit any field and save.",
      target: "setup-config",
      onEnter: () => {
        actions.selectForActivation();
        return undefined;
      },
    },
    {
      title: "Take it live",
      body: "Section 2 controls whether citizens can see the crisis. The controls here let you activate now, schedule a moment, or auto-activate once building data finishes loading. Deactivating later hides it again but keeps every report intact.",
      target: "setup-lifecycle",
    },
    {
      title: "Building shapes and the form",
      body: "Section 3 is where you load or reload building footprints and tune the report form citizens fill in. All of it stays editable while the crisis is live.",
      target: "setup-ingest",
    },
    // The final button hands off to the "Chapter 1 complete" interlude in
    // AdminApp, so there is no closing coachmark.
  ];
}

// The interactive side effects the dashboard chapter drives. Each returns a
// cleanup that reverts the demo when its step is left.
export interface DashboardDemo {
  /** Apply the sample damage filter; returns a cleanup that clears it. */
  runFilterDemo: () => () => void;
  /** Zoom the map into part of the crisis; returns a cleanup that re-fits. */
  runZoomDemo: () => () => void;
  /** Switch the Advanced tools panel to a given tab so its content shows
   *  while we point at it. */
  showTool: (tab: "summary" | "chat" | "snapshots" | "export" | "detail") => void;
}

// Chapter 2: the dashboard. Two freeform live-demo steps apply a filter and a
// map zoom so the numbers, charts and map visibly respond; the rest walk the
// controls in reading order, then each advanced tool.
export function buildDashboardSteps(
  demo: DashboardDemo,
  opts: { hasAnomalies: boolean; filterLabel: string },
): TourStep[] {
  const steps: TourStep[] = [
    {
      title: "Your command center",
      body: "The dashboard reads one crisis at a time. Pick it here and the map, numbers, and reports all follow.",
      target: "crisis",
    },
    {
      title: "The headline numbers",
      body: "Total reports, the devices behind them, buildings affected, and the split by severity.",
      target: "kpi",
    },
    {
      freeform: true,
      highlight: "filters",
      tag: "Live demo",
      title: "Toggle a filter, everything moves",
      body: `Watch closely: in the boxed filter bar we just set the Damage filter to ${opts.filterLabel}. The headline numbers up top and the charts on the left both redrew to match.`,
      onEnter: demo.runFilterDemo,
    },
    {
      freeform: true,
      highlight: ["kpi", "analytics"],
      tag: "Live demo",
      title: "Zoom in, the view recomputes",
      body: "Now we're zooming into one area of the map. Watch the headline numbers and the charts on the left: they recompute for just what's in view, so a single neighborhood reads as easily as the whole crisis. We'll zoom back out.",
      onEnter: demo.runZoomDemo,
    },
    {
      title: "Search by meaning",
      body: "Describe what you're after, not exact words. “Flooded school” also surfaces “water damage to the classroom.”",
      target: "search",
    },
    {
      title: "How strict the search match is",
      body: "This tunes the semantic search above: loosen to cast a wider net, or tighten to keep only the closest matches. Balanced suits most searches.",
      target: "strictness",
    },
    {
      title: "Narrow with filters",
      body: "Filters stack on top of your semantic search to narrow it further, never replacing it: damage, time, debris, how a report was located, and infrastructure type.",
      target: "filters",
    },
    {
      title: "Map or list",
      body: "Flip between the interactive map and a sortable table of every matching report.",
      target: "view",
    },
    {
      title: "Jump to a place",
      body: "Search an area to zoom and filter the reports to it. Try “Gaziantep” to focus on that area.",
      target: "location-search",
    },
    {
      title: "What the map shows",
      body: "Switch between points, a density heatmap, the affected-buildings layer, or all together.",
      target: "map-modes",
    },
    {
      title: "The analysis column",
      body: "Every figure here reflects your current view: the filters you set plus the area shown on the map. Change either and they all recompute together.",
      target: "analytics",
    },
    {
      title: "The advanced tools",
      body: "This panel holds four tools over your current view: an AI Summary that clusters reports into themes, an Analysis tool for priority areas and downloadable PDF reports, GeoJSON Export for QGIS, and Detail for any report you click. (Chat is coming soon.)",
      target: "inspector",
      onEnter: () => {
        demo.showTool("summary");
        return undefined;
      },
    },
  ];

  if (opts.hasAnomalies) {
    steps.push({
      title: "Surface the outliers",
      body: "The platform flags reports that read as unusual against the rest. Toggle this to focus on just those.",
      target: "anomaly",
    });
  }

  steps.push({
    title: "You're ready",
    body: "That's the tour. Replay it anytime from Walkthrough in the top bar. Now dive in.",
    onEnter: () => {
      demo.showTool("summary");
      return undefined;
    },
  });

  return steps;
}
