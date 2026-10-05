import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/reports", () => ({ deleteAllReports: vi.fn() }));
vi.mock("../lib/db", () => ({ clearAllReports: vi.fn() }));
vi.mock("../lib/clientId", () => ({ getClientId: vi.fn(async () => "client-1") }));
vi.mock("../lib/citizenData", () => ({ clearCitizenLocalData: vi.fn() }));

import { deleteAllReports } from "../api/reports";
import { clearCitizenLocalData } from "../lib/citizenData";
import { clearAllReports } from "../lib/db";
import { SettingsScreen } from "./SettingsScreen";

function renderScreen() {
  render(
    <SettingsScreen
      onBack={() => {}}
      onChangeLanguage={() => {}}
      onChangeCrisis={() => {}}
      onPrivacy={() => {}}
      activeCrisis={null}
    />,
  );
}

async function confirmDelete() {
  await userEvent.click(screen.getByRole("button", { name: "Delete all my reports" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete everything" }));
}

beforeEach(() => {
  vi.mocked(deleteAllReports).mockReset().mockResolvedValue("ok");
  vi.mocked(clearAllReports).mockReset().mockResolvedValue(undefined);
  vi.mocked(clearCitizenLocalData).mockReset();
});

describe("SettingsScreen delete my data", () => {
  it("wipes server, IndexedDB and localStorage, then shows the success screen", async () => {
    renderScreen();
    await confirmDelete();

    expect(await screen.findByText("Your data has been deleted.")).toBeInTheDocument();
    expect(deleteAllReports).toHaveBeenCalledWith("client-1");
    expect(clearAllReports).toHaveBeenCalled();
    expect(clearCitizenLocalData).toHaveBeenCalled();
  });

  it("does not claim success when the local wipe fails", async () => {
    vi.mocked(clearAllReports).mockRejectedValue(new Error("idb blocked"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderScreen();
    await confirmDelete();

    expect(screen.queryByText("Your data has been deleted.")).not.toBeInTheDocument();
  });
});
