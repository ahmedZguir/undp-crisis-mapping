// Run work after first paint. Safari/iOS WKWebView lack `requestIdleCallback`, hence the setTimeout shim.

type IdleHandle = { cancel: () => void };

const FALLBACK_DELAY_MS = 1;

export function onIdle(fn: () => void, timeout = 2000): IdleHandle {
  const w = window as typeof window & {
    requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  if (typeof w.requestIdleCallback === "function") {
    const id = w.requestIdleCallback(fn, { timeout });
    return {
      cancel: () => w.cancelIdleCallback?.(id),
    };
  }
  const id = window.setTimeout(fn, FALLBACK_DELAY_MS);
  return { cancel: () => window.clearTimeout(id) };
}
