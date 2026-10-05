import { type ReactNode, Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchCrisisForm } from "./api/crisisForm";
import { ConfirmationScreen } from "./components/ConfirmationScreen";
import { ConnectionPill } from "./components/ConnectionPill";
import { ConsentScreen } from "./components/ConsentScreen";
import { CrisisAssignBanner } from "./components/CrisisAssignBanner";
import { CrisisPickerScreen } from "./components/CrisisPickerScreen";
import { DraftsScreen } from "./components/DraftsScreen";
import { HomeChoiceScreen } from "./components/HomeChoiceScreen";
import { LangPickerScreen } from "./components/LangPickerScreen";
import { MapLoadingIndicator } from "./components/MapLoadingIndicator";
import { MyReportsScreen } from "./components/MyReportsScreen";
import { PreviewBanner } from "./components/PreviewBanner";
import { PrivacyPolicyScreen } from "./components/PrivacyPolicyScreen";
import { ReportForm } from "./components/ReportForm";
import { ServiceWorkerAutoUpdate } from "./components/ServiceWorkerAutoUpdate";
import { SettingsScreen } from "./components/SettingsScreen";
import { TabBar, type TabId } from "./components/TabBar";
import { useCrisisFormRefresh } from "./hooks/useCrisisFormRefresh";
import { useCrisisPersistence } from "./hooks/useCrisisPersistence";
import { useDrafts } from "./hooks/useDrafts";
import { useOnlineStatus } from "./hooks/useOnlineStatus";
import { useOutboxFlushTriggers } from "./hooks/useOutboxFlushTriggers";
import { useReportNotice } from "./hooks/useReportNotice";
import { useScoringWatch } from "./hooks/useScoringWatch";
import { useUnassignedCrisisResolver } from "./hooks/useUnassignedCrisisResolver";
import { getClientId } from "./lib/clientId";
import { hasConsented } from "./lib/consent";
import { putPhoto } from "./lib/db";
import { LANG_PICKED_KEY, langStore } from "./lib/langGate";
import { DEFAULT_MAP_CENTER } from "./lib/mapDefaults";
import { runPrecache, warmOsmTiles, warmPmtiles } from "./lib/precache";
import { preloadStepIcons } from "./lib/preloadIcons";
import { detectPreviewMode } from "./lib/previewMode";
import { sendTileVersion } from "./lib/sendTileVersion";
import { warmExifr, warmHomeData, warmMapChunk } from "./lib/warm";
import { outbox } from "./platform/outbox";
import { isNativePlatform } from "./platform/platformInfo";
import { share } from "./platform/share";
import type { Crisis, FormSchema, FormState, ReportPayload } from "./types";
import "./styles/tokens.css";
import "./App.css";

// Web-only (native WebView path is always `/`); lazy so admin code and admin.css
// stay out of the citizen and native bundles.
const AdminApp = lazy(() =>
  import("./components/admin/AdminApp").then((m) => ({ default: m.AdminApp })),
);

// Mirrors admin.css `--c-bg`, which only arrives with the lazy admin chunk.
const ADMIN_SHELL_BG = "#0e1218";

// Dark fill while the admin chunk loads, to avoid a white flash.
function AdminFallback() {
  return (
    <div style={{ position: "fixed", inset: 0, background: ADMIN_SHELL_BG }} aria-hidden="true" />
  );
}

// Below this the admin layout is unusable, so all of /admin (login too) shows a notice.
const ADMIN_MIN_WIDTH = 1000;

// Only mounted on the /admin path, so citizens never pay for the listener.
function useIsWide(minWidth: number) {
  const [wide, setWide] = useState(() =>
    typeof window === "undefined" ? true : window.innerWidth >= minWidth,
  );
  useEffect(() => {
    const mq = window.matchMedia(`(min-width: ${minWidth}px)`);
    const onChange = () => setWide(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [minWidth]);
  return wide;
}

// Inline styles: admin.css is in the lazy chunk, which is deliberately not fetched here.
function AdminTooSmall() {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: ADMIN_SHELL_BG,
        color: "#e6edf3",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        padding: 24,
      }}
    >
      <div style={{ maxWidth: 420 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 8px" }}>Screen too small</h1>
        <p style={{ fontSize: 14, lineHeight: 1.5, color: "#9aa7b4", margin: 0 }}>
          The coordinator console is built for desktop. Open it on a screen at least{" "}
          {ADMIN_MIN_WIDTH}px wide — a laptop or desktop — to continue.
        </p>
      </div>
    </div>
  );
}

// Never enters Suspense on a narrow screen, so phones never fetch the admin chunk.
function AdminScreenGate() {
  const wide = useIsWide(ADMIN_MIN_WIDTH);
  if (!wide) return <AdminTooSmall />;
  return (
    <Suspense fallback={<AdminFallback />}>
      <AdminApp />
    </Suspense>
  );
}

// maplibre + pmtiles (~276 KB gz) kept out of first paint; lib/warm.ts prefetches it.
const BrowseMap = lazy(() =>
  import("./components/BrowseMap").then((m) => ({ default: m.BrowseMap })),
);

function MapFallback() {
  const { t } = useTranslation();
  return (
    <MapLoadingIndicator
      label={t("browseMap.loading", { defaultValue: "Loading map…" })}
      style={{ position: "fixed", inset: 0, background: "var(--c-surface)", gap: 10 }}
    />
  );
}

type ActiveScreen =
  | "lang-picker"
  | "consent"
  | "privacy"
  | "crisis-picker"
  | "home-choice"
  | "report-form"
  | "browse-map"
  | "confirmation"
  | "share-hydrate"
  | "drafts-list"
  | "my-reports"
  | "settings";

const TAB_SCREENS: ReadonlyArray<ActiveScreen> = [
  "home-choice",
  "my-reports",
  "settings",
  "report-form",
  "browse-map",
];

// Screen each tab opens; the inverse of screenToTab.
const TAB_HOME_SCREEN: Record<TabId, ActiveScreen> = {
  report: "home-choice",
  "my-reports": "my-reports",
  settings: "settings",
};

function screenToTab(s: ActiveScreen): TabId {
  if (s === "my-reports") return "my-reports";
  if (s === "settings") return "settings";
  return "report";
}

// A POLICY_VERSION bump re-routes returning users through consent.
function postLangScreen(): ActiveScreen {
  return hasConsented() ? "home-choice" : "consent";
}

function screenAfterLangGate(): ActiveScreen {
  return langStore().getItem(LANG_PICKED_KEY) ? postLangScreen() : "lang-picker";
}

function getInitialScreen(): ActiveScreen {
  return share.isShareLaunch() ? "share-hydrate" : screenAfterLangGate();
}

const emptyFormState: FormState = {
  photo: null,
  damage_class: null,
  infra_type: [],
  infra_type_other: "",
  infra_name: "",
  description: "",
  crisis_nature: null,
  crisis_nature_type: null,
  crisis_nature_other: "",
  debris: null,
  electricity: null,
  health_services: null,
  pressing_needs: [],
  crisis_id: "",
  building_id: undefined,
  latitude: null,
  longitude: null,
  route_description: "",
  photo_metadata: null,
  generic_answers: {},
};

function App() {
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/admin")) {
    return <AdminScreenGate />;
  }
  // Standalone privacy notice at a real URL, independent of the in-app gate.
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/privacy")) {
    return <PrivacyPolicyScreen onBack={() => window.location.assign("/")} />;
  }
  const preview = typeof window !== "undefined" ? detectPreviewMode() : null;
  if (preview) {
    return <PreviewApp handoff={preview} />;
  }
  return <CitizenApp />;
}

function PreviewApp({ handoff }: { handoff: NonNullable<ReturnType<typeof detectPreviewMode>> }) {
  // Preview never hits the network, so non-form fields are stubs.
  const crisis = {
    id: handoff.crisisId,
    name: "Preview",
    pmtiles_url: null,
    overture_release_pinned: null,
    public_visibility: "none" as const,
    form_schema: handoff.schema,
    form_version: handoff.version,
  };
  return (
    <>
      <PreviewBanner />
      <ReportForm
        crisis={crisis}
        previewMode
        onComplete={() => {}}
        onCancel={() => window.close()}
      />
    </>
  );
}

function CitizenApp() {
  const { i18n } = useTranslation();
  // Schema labels are resolved server-side in this locale.
  const activeLocale = i18n.resolvedLanguage ?? i18n.language ?? "en";
  const [activeScreen, setActiveScreen] = useState<ActiveScreen>(getInitialScreen);
  const [reportId, setReportId] = useState<string>("");
  const persistence = useCrisisPersistence();
  const drafts = useDrafts();
  const online = useOnlineStatus();
  const { badge: myReportsBadge } = useReportNotice();
  // App-wide so points/badges refresh while any report is still being scored.
  useScoringWatch();

  const [reportSnapshot, setReportSnapshot] = useState<FormState | null>(null);
  const [draftStep, setDraftStep] = useState(0);
  // Pinned at draft creation so a resumed draft renders against its original form.
  const [draftPinned, setDraftPinned] = useState<{
    schema: FormSchema;
    version: number;
  } | null>(null);
  // A resumed draft uses its own crisis, not the globally-selected one.
  const [draftCrisis, setDraftCrisis] = useState<Crisis | null>(null);
  const [activeDraftId, setActiveDraftId] = useState<string | null>(null);
  const [lastSubmitQueued, setLastSubmitQueued] = useState(false);
  const [pickerReturnTo, setPickerReturnTo] = useState<ActiveScreen | null>(null);
  // null = normal first-run flow (consent/home).
  const [langReturnTo, setLangReturnTo] = useState<ActiveScreen | null>(null);
  const [privacyReturnTo, setPrivacyReturnTo] = useState<ActiveScreen>("home-choice");

  useOutboxFlushTriggers();

  useEffect(() => {
    if (activeScreen !== "share-hydrate") return;
    let cancelled = false;
    void (async () => {
      const photo = await share.consumeSharedPhoto();
      if (cancelled) return;
      if (window.history.replaceState) {
        // Strip ?share=1 so a refresh doesn't re-enter the hydrate flow.
        window.history.replaceState({}, "", "/");
      }
      if (!photo) {
        setActiveScreen(screenAfterLangGate());
        return;
      }
      const id = drafts.newDraftId();
      setActiveDraftId(id);
      const hydrated: FormState = { ...emptyFormState, photo };
      drafts.save(id, hydrated, 0);
      setReportSnapshot(hydrated);
      setDraftStep(0);
      setActiveScreen("report-form");
    })();
    return () => {
      cancelled = true;
    };
  }, [activeScreen, drafts]);

  // Ref so the startup effect can read it without depending on it.
  const initialTileVersionRef = useRef(persistence.stored?.overture_release_pinned ?? null);

  useEffect(() => {
    void navigator.serviceWorker?.ready?.then(() => {
      sendTileVersion(initialTileVersionRef.current);
    });
  }, []);

  const saveCrisisAndSync = useCallback(
    (crisis: Crisis) => {
      persistence.save(crisis);
      sendTileVersion(crisis.overture_release_pinned);
      // Yield a tick so the SW applies SET_CRISIS_TILE_VERSION before warmPmtiles
      // issues Range fetches; otherwise tiles land in pmtiles-unknown.
      setTimeout(() => void warmPmtiles(crisis.pmtiles_url), 0);
      // Fetch the form in the active locale so generic-page text is localized.
      void (async () => {
        try {
          const form = await fetchCrisisForm(crisis.id, activeLocale);
          if (form) persistence.save({ ...crisis, ...form });
        } catch {
          // Offline: keep the stored crisis.
        }
      })();
    },
    [persistence, activeLocale],
  );

  useCrisisFormRefresh(persistence, activeLocale);

  const [unassignedCount, setUnassignedCount] = useUnassignedCrisisResolver(
    persistence,
    saveCrisisAndSync,
  );

  // Home is the hub: warm what later screens need. Warms are idle-scheduled
  // and idempotent (lib/warm.ts).
  useEffect(() => {
    if (activeScreen !== "home-choice") return;
    // Icons get the network first; exifr is tiny and needed next; the heavy map
    // chunk waits for the icons so it does not preempt them.
    const iconsReady = preloadStepIcons();
    warmExifr();
    void iconsReady.then(() => warmMapChunk());
    // Resolve the client id now so per-client caches are readable on mount.
    void getClientId()
      .catch(() => null)
      .then((clientId) => warmHomeData(persistence.stored?.id ?? null, clientId));
  }, [activeScreen, persistence.stored?.id]);

  // Backstop for entry paths that skip home (share-target deep link).
  useEffect(() => {
    if (activeScreen === "report-form") {
      warmExifr();
      warmMapChunk();
    }
  }, [activeScreen]);

  const handleSubmit = useCallback(
    async (payload: ReportPayload): Promise<{ submissionId: string; queued: boolean }> => {
      // Defensive: the form should always have a draft id by now.
      const draftId = activeDraftId ?? drafts.newDraftId();
      if (!activeDraftId) setActiveDraftId(draftId);
      const wasOnline = typeof navigator === "undefined" ? true : navigator.onLine;
      // Cancel pending autosaves first, or a late save would resurrect the draft
      // row after promoteDraftToOutbox deletes it.
      await drafts.cancelPending(draftId);
      // Photo is optional under the minimum-content rule.
      const photoId = payload.photo ? await putPhoto(payload.photo) : null;
      const { photo: _photo, ...rest } = payload;
      void _photo;
      const { clientSubmissionId } = await outbox.enqueueFromDraft(draftId, photoId, rest);
      void outbox.requestFlush("post-submit");
      setLastSubmitQueued(!wasOnline);
      return { submissionId: clientSubmissionId, queued: !wasOnline };
    },
    [activeDraftId, drafts],
  );

  const continueDraft = useCallback(
    async (id: string) => {
      const loaded = await drafts.load(id);
      if (!loaded) return;
      setActiveDraftId(id);
      setReportSnapshot(loaded.state);
      setDraftStep(loaded.step);
      setDraftPinned(
        loaded.schema && typeof loaded.version === "number"
          ? { schema: loaded.schema, version: loaded.version }
          : null,
      );
      setDraftCrisis(loaded.crisis ?? null);
      setActiveScreen("report-form");
    },
    [drafts],
  );

  const startNewReport = useCallback(() => {
    const id = drafts.newDraftId();
    setActiveDraftId(id);
    setReportSnapshot(null);
    setDraftStep(0);
    setDraftPinned(null);
    setDraftCrisis(null);
    setActiveScreen("report-form");
  }, [drafts]);

  const showConnectionPill =
    activeScreen !== "share-hydrate" &&
    activeScreen !== "lang-picker" &&
    activeScreen !== "consent";
  const showTabBar = TAB_SCREENS.includes(activeScreen);
  const wrap = (node: ReactNode) => (
    <>
      {node}
      {showConnectionPill && (
        <div
          style={{
            position: "fixed",
            top: "calc(env(safe-area-inset-top, 0px) + 6px)",
            insetInlineEnd: "calc(env(safe-area-inset-right, 0px) + 6px)",
            zIndex: 1500,
            pointerEvents: "none",
          }}
        >
          <ConnectionPill />
        </div>
      )}
      {unassignedCount > 0 && (
        <CrisisAssignBanner
          unassignedCount={unassignedCount}
          onPickCrisis={() => setActiveScreen("crisis-picker")}
          onDismiss={() => setUnassignedCount(0)}
        />
      )}
      {showTabBar && (
        <TabBar
          active={screenToTab(activeScreen)}
          myReportsBadge={myReportsBadge}
          onSelect={(tab) => setActiveScreen(TAB_HOME_SCREEN[tab])}
        />
      )}
      {/* Web-only: native never registers the SW (precache fails on capacitor://localhost). */}
      {!isNativePlatform() && <ServiceWorkerAutoUpdate activeScreen={activeScreen} />}
    </>
  );

  if (activeScreen === "share-hydrate") {
    return wrap(null);
  }

  if (activeScreen === "lang-picker") {
    return wrap(
      <LangPickerScreen
        onBack={
          langReturnTo
            ? () => {
                // From Settings: the picked flag was cleared on entry, so restore it
                // before returning without a language change.
                langStore().setItem(LANG_PICKED_KEY, i18n.language || "en");
                const back = langReturnTo;
                setLangReturnTo(null);
                setActiveScreen(back);
              }
            : undefined
        }
        onComplete={() => {
          // Best-effort offline pre-cache (crises list, pmtiles header).
          void runPrecache().then((result) => {
            if (result.autoSelectedCrisis) {
              saveCrisisAndSync(result.autoSelectedCrisis);
            }
          });
          const next = langReturnTo ?? postLangScreen();
          setLangReturnTo(null);
          setActiveScreen(next);
        }}
      />,
    );
  }

  if (activeScreen === "consent") {
    return wrap(
      <ConsentScreen
        onAgree={() => {
          // Warm base tiles so the first map open is not cold: crisis PMTiles centre,
          // else the deployment default.
          const storedCrisis = persistence.stored;
          if (storedCrisis?.pmtiles_url) {
            setTimeout(() => void warmPmtiles(storedCrisis.pmtiles_url), 0);
          } else {
            void warmOsmTiles(DEFAULT_MAP_CENTER[0], DEFAULT_MAP_CENTER[1]);
          }
          setActiveScreen("home-choice");
        }}
        onReadPolicy={() => {
          setPrivacyReturnTo("consent");
          setActiveScreen("privacy");
        }}
      />,
    );
  }

  if (activeScreen === "privacy") {
    return wrap(
      <PrivacyPolicyScreen
        onBack={() => setActiveScreen(privacyReturnTo)}
        onAgree={privacyReturnTo === "consent" ? () => setActiveScreen("home-choice") : undefined}
      />,
    );
  }

  if (activeScreen === "crisis-picker") {
    return wrap(
      <CrisisPickerScreen
        onBack={() => {
          const back = pickerReturnTo ?? "home-choice";
          setPickerReturnTo(null);
          setActiveScreen(back);
        }}
        onPick={(crisis) => {
          saveCrisisAndSync(crisis);
          const next = pickerReturnTo ?? (reportSnapshot ? "report-form" : "home-choice");
          setPickerReturnTo(null);
          setActiveScreen(next);
        }}
      />,
    );
  }

  if (activeScreen === "home-choice") {
    return wrap(
      <HomeChoiceScreen
        drafts={drafts.drafts}
        onContinueDraft={(id) => void continueDraft(id)}
        onViewAllDrafts={() => setActiveScreen("drafts-list")}
        onReport={startNewReport}
        onMap={() => setActiveScreen("browse-map")}
        onCrisis={() => setActiveScreen("crisis-picker")}
      />,
    );
  }

  if (activeScreen === "drafts-list") {
    return wrap(
      <DraftsScreen
        drafts={drafts.drafts}
        onContinue={(id) => void continueDraft(id)}
        onDiscard={(id) => void drafts.discard(id)}
        onBack={() => setActiveScreen("home-choice")}
      />,
    );
  }

  if (activeScreen === "my-reports") {
    return wrap(
      <MyReportsScreen
        onBack={() => setActiveScreen("home-choice")}
        onContinueDraft={(id) => void continueDraft(id)}
        onDiscardDraft={(id) => void drafts.discard(id)}
        onNewReport={startNewReport}
      />,
    );
  }

  if (activeScreen === "settings") {
    return wrap(
      <SettingsScreen
        onBack={() => setActiveScreen("home-choice")}
        onChangeLanguage={() => {
          langStore().removeItem(LANG_PICKED_KEY);
          setLangReturnTo("settings");
          setActiveScreen("lang-picker");
        }}
        onChangeCrisis={() => {
          setPickerReturnTo("settings");
          setActiveScreen("crisis-picker");
        }}
        onPrivacy={() => {
          setPrivacyReturnTo("settings");
          setActiveScreen("privacy");
        }}
        activeCrisis={persistence.stored ?? null}
      />,
    );
  }

  if (activeScreen === "report-form") {
    const saveActive = (
      state: FormState,
      step: number,
      schema?: FormSchema,
      version?: number,
      crisis?: Crisis | null,
    ) => {
      if (!activeDraftId) return;
      drafts.save(activeDraftId, state, step, schema, version, crisis ?? undefined);
    };
    const discardActive = async () => {
      if (!activeDraftId) return;
      await drafts.discard(activeDraftId);
    };
    // A resumed draft keeps its own crisis and pinned schema, even if the user has
    // since switched crisis or the crisis published a new form_version.
    const baseCrisis = draftCrisis ?? persistence.stored;
    const crisisForForm =
      baseCrisis && draftPinned
        ? { ...baseCrisis, form_schema: draftPinned.schema, form_version: draftPinned.version }
        : baseCrisis;
    return wrap(
      <ReportForm
        crisis={crisisForForm}
        online={online}
        onCrisisPick={(picked) => {
          // Switching crisis mid-form drops the draft's pinned crisis and schema.
          setDraftCrisis(null);
          setDraftPinned(null);
          saveCrisisAndSync(picked);
        }}
        initialState={reportSnapshot ?? undefined}
        initialStep={draftStep}
        saveDraft={saveActive}
        discardDraft={discardActive}
        onSubmit={handleSubmit}
        onComplete={(id, queued) => {
          setReportId(id);
          setReportSnapshot(null);
          setActiveDraftId(null);
          setLastSubmitQueued(queued);
          setActiveScreen("confirmation");
        }}
        onCancel={() => {
          setReportSnapshot(null);
          setActiveScreen("home-choice");
        }}
      />,
    );
  }

  if (activeScreen === "browse-map") {
    return wrap(
      <Suspense fallback={<MapFallback />}>
        <BrowseMap
          onBack={() => setActiveScreen("home-choice")}
          onChangeCrisis={() => {
            setPickerReturnTo("browse-map");
            setActiveScreen("crisis-picker");
          }}
        />
      </Suspense>,
    );
  }

  if (activeScreen === "confirmation") {
    return wrap(
      <ConfirmationScreen
        reportId={reportId}
        queued={lastSubmitQueued}
        onHome={() => {
          setReportId("");
          setActiveScreen("home-choice");
        }}
        onReportAnother={startNewReport}
      />,
    );
  }

  return null;
}

export default App;
