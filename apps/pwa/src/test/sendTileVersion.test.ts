import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sendTileVersion } from "../lib/sendTileVersion";

describe("sendTileVersion", () => {
  let mockController: { postMessage: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    mockController = { postMessage: vi.fn() };
    vi.stubGlobal("navigator", {
      serviceWorker: { controller: mockController },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts SET_CRISIS_TILE_VERSION with the given version string", () => {
    sendTileVersion("2024-07-01");
    expect(mockController.postMessage).toHaveBeenCalledWith({
      type: "SET_CRISIS_TILE_VERSION",
      overture_release_pinned: "2024-07-01",
    });
  });

  it("posts SET_CRISIS_TILE_VERSION with null when no version is provided", () => {
    sendTileVersion(null);
    expect(mockController.postMessage).toHaveBeenCalledWith({
      type: "SET_CRISIS_TILE_VERSION",
      overture_release_pinned: null,
    });
  });

  it("silent no-op when controller is null (SW not yet active)", () => {
    vi.stubGlobal("navigator", {
      serviceWorker: { controller: null },
    });
    expect(() => sendTileVersion("2024-07-01")).not.toThrow();
    expect(mockController.postMessage).not.toHaveBeenCalled();
  });

  it("silent no-op when serviceWorker is unavailable", () => {
    vi.stubGlobal("navigator", {});
    expect(() => sendTileVersion("2024-07-01")).not.toThrow();
  });
});
