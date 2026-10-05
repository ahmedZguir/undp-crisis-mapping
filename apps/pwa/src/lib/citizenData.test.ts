import { beforeEach, describe, expect, it } from "vitest";
import { reporterStatsKey } from "../api/reporter";
import { clearCitizenLocalData } from "./citizenData";
import { REPORTS_CACHE_UPDATED_EVENT } from "./reportsCache";

beforeEach(() => {
  localStorage.clear();
});

describe("clearCitizenLocalData", () => {
  it("removes cached report history, stats, notice baseline and last location", () => {
    localStorage.setItem("rid-reports-cache:client-a", '{"items":[{"location":{"lat":1}}]}');
    localStorage.setItem("rid-reports-cache:client-old", '{"items":[]}');
    localStorage.setItem(reporterStatsKey("client-a"), '{"data":{"total_reports":3}}');
    localStorage.setItem("rid-report-notice-seen", '{"submittedIds":["r1"]}');
    localStorage.setItem("rid-last-known-location", '{"lat":1,"lng":2}');

    clearCitizenLocalData();

    expect(Object.keys(localStorage).filter((k) => k.startsWith("rid-"))).toEqual([]);
  });

  it("keeps preferences and public caches", () => {
    localStorage.setItem("rid-theme", "dark");
    localStorage.setItem("rid-lang-picked", "1");
    localStorage.setItem("rid-consent-version", "3");
    localStorage.setItem("rid-crises-cache", "[]");

    clearCitizenLocalData();

    expect(localStorage.getItem("rid-theme")).toBe("dark");
    expect(localStorage.getItem("rid-lang-picked")).toBe("1");
    expect(localStorage.getItem("rid-consent-version")).toBe("3");
    expect(localStorage.getItem("rid-crises-cache")).toBe("[]");
  });

  it("tells the My Reports screen its cache changed", () => {
    let fired = 0;
    const onUpdate = () => {
      fired += 1;
    };
    window.addEventListener(REPORTS_CACHE_UPDATED_EVENT, onUpdate);
    clearCitizenLocalData();
    window.removeEventListener(REPORTS_CACHE_UPDATED_EVENT, onUpdate);
    expect(fired).toBe(1);
  });
});
