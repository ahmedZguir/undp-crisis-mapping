import { useRegisterSW as defaultUseRegisterSW } from "virtual:pwa-register/react";
import { useEffect, useRef } from "react";

const HOME_SCREEN = "home-choice";

export type UseRegisterSWLike = () => {
  offlineReady: [boolean, (v: boolean) => void];
  needRefresh: [boolean, (v: boolean) => void];
  updateServiceWorker: (reloadPage?: boolean) => Promise<void> | void;
};

interface ServiceWorkerAutoUpdateProps {
  activeScreen: string;
  useRegisterSW?: UseRegisterSWLike;
}

/** Applies a waiting SW update (reload) the next time the user is on the home screen. */
export function ServiceWorkerAutoUpdate({
  activeScreen,
  useRegisterSW = defaultUseRegisterSW as unknown as UseRegisterSWLike,
}: ServiceWorkerAutoUpdateProps) {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();

  const updateTriggeredRef = useRef(false);

  useEffect(() => {
    if (!needRefresh) return;
    if (activeScreen !== HOME_SCREEN) return;
    if (updateTriggeredRef.current) return;
    updateTriggeredRef.current = true;
    void updateServiceWorker(true);
  }, [needRefresh, activeScreen, updateServiceWorker]);

  return null;
}
