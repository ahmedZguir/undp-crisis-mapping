import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/reports", () => ({ getCrises: vi.fn() }));
vi.mock("../lib/precache", () => ({ readCrisesCache: vi.fn() }));

import { getCrises } from "../api/reports";
import { readCrisesCache } from "../lib/precache";
import type { Crisis } from "../types";
import { CrisisPickerScreen } from "./CrisisPickerScreen";

const crisis = { id: "c1", name: "Flood" } as Crisis;

function renderScreen() {
  return render(<CrisisPickerScreen onPick={() => {}} onBack={() => {}} />);
}

beforeEach(() => {
  vi.mocked(getCrises).mockReset();
  vi.mocked(readCrisesCache).mockReset().mockReturnValue(null);
});

describe("CrisisPickerScreen load", () => {
  it("stops after an unmount during the fetch", async () => {
    let reject!: (e: Error) => void;
    vi.mocked(getCrises).mockReturnValue(
      new Promise((_, rej) => {
        reject = rej;
      }),
    );
    const { unmount } = renderScreen();
    unmount();
    reject(new Error("offline"));
    await new Promise((r) => setTimeout(r, 0));

    expect(readCrisesCache).not.toHaveBeenCalled();
  });

  it("falls back to the cached list when the fetch fails", async () => {
    vi.mocked(getCrises).mockRejectedValue(new Error("offline"));
    vi.mocked(readCrisesCache).mockReturnValue([crisis]);
    renderScreen();

    expect(await screen.findByText("Flood")).toBeTruthy();
  });

  it("shows the list from the network", async () => {
    vi.mocked(getCrises).mockResolvedValue([crisis]);
    renderScreen();

    await waitFor(() => expect(screen.getByText("Flood")).toBeTruthy());
    expect(readCrisesCache).not.toHaveBeenCalled();
  });
});
