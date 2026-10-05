import { render } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ServiceWorkerAutoUpdate, type UseRegisterSWLike } from "./ServiceWorkerAutoUpdate";

function makeStubHook(
  initial: {
    offlineReady?: boolean;
    needRefresh?: boolean;
  } = {},
): {
  hook: UseRegisterSWLike;
  updateServiceWorker: ReturnType<typeof vi.fn>;
  setOfflineReady: ((v: boolean) => void) | null;
  setNeedRefresh: ((v: boolean) => void) | null;
} {
  const updateServiceWorker = vi.fn().mockResolvedValue(undefined);
  let setOfflineReadyCb: ((v: boolean) => void) | null = null;
  let setNeedRefreshCb: ((v: boolean) => void) | null = null;
  const hook: UseRegisterSWLike = () => {
    const [offlineReady, setOfflineReady] = useState(initial.offlineReady ?? false);
    const [needRefresh, setNeedRefresh] = useState(initial.needRefresh ?? false);
    setOfflineReadyCb = setOfflineReady;
    setNeedRefreshCb = setNeedRefresh;
    return {
      offlineReady: [offlineReady, setOfflineReady],
      needRefresh: [needRefresh, setNeedRefresh],
      updateServiceWorker,
    };
  };
  return {
    hook,
    updateServiceWorker,
    get setOfflineReady() {
      return setOfflineReadyCb;
    },
    get setNeedRefresh() {
      return setNeedRefreshCb;
    },
  };
}

describe("ServiceWorkerAutoUpdate — deferred reload on update", () => {
  it("does not call updateServiceWorker when an update is pending mid-report", () => {
    const { hook, updateServiceWorker } = makeStubHook({ needRefresh: true });
    render(<ServiceWorkerAutoUpdate activeScreen="report-form" useRegisterSW={hook} />);

    expect(updateServiceWorker).not.toHaveBeenCalled();
  });

  it("calls updateServiceWorker(true) exactly once when the user returns to home-choice", async () => {
    const { hook, updateServiceWorker } = makeStubHook({ needRefresh: true });
    const { rerender } = render(
      <ServiceWorkerAutoUpdate activeScreen="report-form" useRegisterSW={hook} />,
    );
    expect(updateServiceWorker).not.toHaveBeenCalled();

    rerender(<ServiceWorkerAutoUpdate activeScreen="home-choice" useRegisterSW={hook} />);

    await Promise.resolve();
    expect(updateServiceWorker).toHaveBeenCalledTimes(1);
    expect(updateServiceWorker).toHaveBeenCalledWith(true);

    // Re-rendering the same screen with the flag still set must not retrigger.
    rerender(<ServiceWorkerAutoUpdate activeScreen="home-choice" useRegisterSW={hook} />);
    await Promise.resolve();
    expect(updateServiceWorker).toHaveBeenCalledTimes(1);
  });
});
