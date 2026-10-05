export function sendTileVersion(overture_release_pinned: string | null): void {
  const controller = navigator?.serviceWorker?.controller;
  if (!controller) return;
  controller.postMessage({ type: "SET_CRISIS_TILE_VERSION", overture_release_pinned });
}
