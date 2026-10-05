import { useCallback, useEffect, useState } from "react";
import "../../styles/admin.css";
import { type OnboardingState, getOnboardingState, markOnboarding } from "../../api/admin";
import { logout, refresh } from "../../api/auth";
import { applyTheme, getActiveTheme } from "../../lib/theme";
import { type AdminTab, AdminTopbar } from "./AdminTopbar";
import { AdminsPage } from "./AdminsPage";
import { CrisesPage } from "./CrisesPage";
import { FormBuilderPage } from "./FormBuilderPage";
import { LoginPage } from "./LoginPage";
import { ReportsPage } from "./ReportsPage";
import { AnalysisPage } from "./analysis/AnalysisPage";
import { ChapterCompleteOverlay } from "./onboarding/ChapterCompleteOverlay";
import { GuidedTour, type TourStep } from "./onboarding/GuidedTour";
import { WelcomeOverlay } from "./onboarding/WelcomeOverlay";

const VALID: AdminTab[] = ["dashboard", "analysis", "crises", "admins"];

function tabFromHash(): AdminTab {
  const raw = window.location.hash.replace(/^#\/?/, "").split("?")[0].trim();
  // `crises/<id>/form` is the per-crisis form builder, shown under the `crises` tab.
  const head = raw.split("/")[0];
  return (VALID as string[]).includes(head) ? (head as AdminTab) : "dashboard";
}

function crisisFormIdFromHash(): string | null {
  const raw = window.location.hash.replace(/^#\/?/, "").split("?")[0].trim();
  const m = raw.match(/^crises\/([0-9a-f-]{36})\/form$/);
  return m ? m[1] : null;
}

type AuthState = "checking" | "anonymous" | "authed";

// First-run walkthrough phases: `welcome` splash; `crises` runs chapter 1 over
// the Crises page; `interlude` shows the "Chapter 1 complete" card;
// `to-dashboard` spotlights the Dashboard tab so the move to chapter 2 is
// guided; `dashboard` hands off to ReportsPage for chapter 2; null is idle.
type OnboardingPhase = "welcome" | "crises" | "interlude" | "to-dashboard" | "dashboard" | null;

// Hand-off coachmark between the interlude and chapter 2, pointing at the Dashboard tab.
const TO_DASHBOARD_STEP: TourStep[] = [
  {
    target: "nav-dashboard",
    title: "Over to the dashboard",
    body: "Chapter 2 reads a live crisis on the Dashboard. Let's open it.",
  },
];

export function AdminApp() {
  const [active, setActive] = useState<AdminTab>(() => tabFromHash());
  const [crisisFormId, setCrisisFormId] = useState<string | null>(() => crisisFormIdFromHash());
  // `checking` while /auth/refresh tries once to recover a session from the cookie.
  const [auth, setAuth] = useState<AuthState>("checking");
  const [obPhase, setObPhase] = useState<OnboardingPhase>(null);
  // Per-account first-run state. `null` until fetched, so the splash and
  // checklist never flash for a coordinator who has already finished.
  const [obState, setObState] = useState<OnboardingState | null>(null);
  // Latched on visiting the analysis page, for the dashboard checklist. Lives
  // here because the analysis route unmounts the dashboard.
  const [analysisVisited, setAnalysisVisited] = useState(false);
  useEffect(() => {
    if (active === "analysis") setAnalysisVisited(true);
  }, [active]);

  useEffect(() => {
    let cancelled = false;
    refresh().then((ok) => {
      if (cancelled) return;
      setAuth(ok ? "authed" : "anonymous");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onHash = () => {
      setActive(tabFromHash());
      setCrisisFormId(crisisFormIdFromHash());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // On failure default to "already onboarded", so we never nag with a splash
  // we can't confirm is needed.
  useEffect(() => {
    if (auth !== "authed") return;
    let cancelled = false;
    getOnboardingState()
      .then((s) => {
        if (!cancelled) setObState(s);
      })
      .catch(() => {
        if (!cancelled) setObState({ walkthrough_completed: true, dashboard_explored: true });
      });
    return () => {
      cancelled = true;
    };
  }, [auth]);

  // Auto-show the splash until the walkthrough is completed; the top-bar
  // button replays it regardless.
  useEffect(() => {
    if (auth === "authed" && obState && !obState.walkthrough_completed) {
      setObPhase("welcome");
    }
  }, [auth, obState]);

  useEffect(() => {
    document.body.classList.add("admin-mode");
    // Apply the theme synchronously so the shell doesn't flash light before
    // the toggle hook attaches.
    applyTheme(getActiveTheme());
    return () => document.body.classList.remove("admin-mode");
  }, []);

  const navigate = useCallback((tab: AdminTab) => {
    window.location.hash = `#/${tab}`;
    setActive(tab);
  }, []);

  const handleLogout = useCallback(async () => {
    await logout();
    window.history.replaceState(null, "", "#/");
    setAuth("anonymous");
  }, []);

  // --- First-run walkthrough handlers ---
  const finishOnboarding = useCallback(() => {
    // Optimistic; a failed server write only means the splash returns next login.
    setObState((prev) => (prev ? { ...prev, walkthrough_completed: true } : prev));
    void markOnboarding({ walkthrough_completed: true }).catch(() => {});
    setObPhase(null);
  }, []);
  const onDashboardExplored = useCallback(() => {
    setObState((prev) => (prev ? { ...prev, dashboard_explored: true } : prev));
    void markOnboarding({ dashboard_explored: true }).catch(() => {});
  }, []);
  const beginWalkthrough = useCallback(() => {
    navigate("crises");
    setObPhase("crises");
  }, [navigate]);
  // Chapter 1 finished: show the interlude (no navigation yet). Skipped: done.
  const onCrisesChapterClose = useCallback(
    (completed: boolean) => {
      if (completed) {
        setObPhase("interlude");
      } else {
        finishOnboarding();
      }
    },
    [finishOnboarding],
  );
  const beginDashboardChapter = useCallback(() => setObPhase("to-dashboard"), []);
  // Hand-off dismissed: open the dashboard for chapter 2. Skipped: end the walkthrough.
  const onToDashboardClose = useCallback(
    (completed: boolean) => {
      if (completed) {
        navigate("dashboard");
        setObPhase("dashboard");
      } else {
        finishOnboarding();
      }
    },
    [navigate, finishOnboarding],
  );
  // Replays from the top, ignoring the saved flag.
  const replayWalkthrough = useCallback(() => setObPhase("welcome"), []);

  if (auth === "checking") {
    return (
      <div
        className="admin-root"
        style={{
          display: "grid",
          placeItems: "center",
          background: "var(--c-surface)",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            color: "var(--c-ink-3)",
            fontSize: 13,
          }}
        >
          <span
            className="spin"
            aria-hidden="true"
            style={{ width: 22, height: 22, color: "var(--c-blue-700)" }}
          />
          <span>Restoring your session…</span>
        </div>
      </div>
    );
  }

  if (auth === "anonymous") {
    return <LoginPage onAuthed={() => setAuth("authed")} />;
  }

  return (
    <div className="admin-root" style={{ display: "flex", flexDirection: "column" }}>
      <AdminTopbar
        active={active}
        onNavigate={navigate}
        onLogout={handleLogout}
        onStartWalkthrough={replayWalkthrough}
      />
      {active === "dashboard" && (
        <ReportsPage
          onboardingActive={obPhase === "dashboard"}
          onOnboardingDashboardDone={finishOnboarding}
          dashboardExplored={obState?.dashboard_explored ?? true}
          onDashboardExplored={onDashboardExplored}
          analysisVisited={analysisVisited}
        />
      )}
      {active === "analysis" && <AnalysisPage />}
      {active === "crises" && !crisisFormId && (
        <CrisesPage
          onboardingActive={obPhase === "crises"}
          onOnboardingCrisesClose={onCrisesChapterClose}
        />
      )}
      {active === "crises" && crisisFormId && (
        <FormBuilderPage
          crisisId={crisisFormId}
          onCancel={() => {
            // CrisesPage reads `?crisis=` on mount and re-selects that crisis.
            window.location.hash = `#/crises?crisis=${crisisFormId}`;
          }}
        />
      )}
      {active === "admins" && <AdminsPage />}

      {/* Each guided chapter lives inside the page it walks (CrisesPage,
          ReportsPage) so its steps can drive that page's state. AdminApp only
          shows the welcome splash and routes between the two chapters. */}
      <WelcomeOverlay
        open={obPhase === "welcome"}
        onStart={beginWalkthrough}
        onSkip={finishOnboarding}
      />
      <ChapterCompleteOverlay
        open={obPhase === "interlude"}
        onContinue={beginDashboardChapter}
        onSkip={finishOnboarding}
      />
      <GuidedTour
        open={obPhase === "to-dashboard"}
        steps={TO_DASHBOARD_STEP}
        chapter="Chapter 2 of 2"
        doneLabel="Open the dashboard"
        onClose={onToDashboardClose}
      />
    </div>
  );
}
