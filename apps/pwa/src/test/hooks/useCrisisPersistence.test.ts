import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useCrisisPersistence } from "../../hooks/useCrisisPersistence";

const KEY = "rid-crisis-picked";

const fullCrisis = {
  id: "c1",
  name: "Quake",
  pmtiles_url: "pmtiles://tiles.example/2024",
  overture_release_pinned: "2024-04-01",
  public_visibility: "aggregate_view" as const,
};

describe("useCrisisPersistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    localStorage.clear();
  });

  it("returns null when nothing is stored", () => {
    const { result } = renderHook(() => useCrisisPersistence());
    expect(result.current.stored).toBeNull();
  });

  it("hydrates from existing localStorage on mount when stored value has pmtiles_url", () => {
    localStorage.setItem(KEY, JSON.stringify(fullCrisis));
    const { result } = renderHook(() => useCrisisPersistence());
    expect(result.current.stored).toEqual(fullCrisis);
  });

  it("hydrates a pre-public_visibility blob with the aggregate_view default", () => {
    const preFieldBlob = {
      id: "c-legacy",
      name: "Legacy",
      pmtiles_url: null,
      overture_release_pinned: null,
    };
    localStorage.setItem(KEY, JSON.stringify(preFieldBlob));
    const { result } = renderHook(() => useCrisisPersistence());
    expect(result.current.stored).toEqual({
      ...preFieldBlob,
      public_visibility: "aggregate_view",
    });
  });

  it("returns null when stored value is missing pmtiles_url (old schema guard)", () => {
    localStorage.setItem(KEY, JSON.stringify({ id: "x", name: "Old Crisis" }));
    const { result } = renderHook(() => useCrisisPersistence());
    expect(result.current.stored).toBeNull();
  });

  it("accepts pmtiles_url: null as a valid stored value", () => {
    const noPmtiles = {
      id: "c2",
      name: "Crisis",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view" as const,
    };
    localStorage.setItem(KEY, JSON.stringify(noPmtiles));
    const { result } = renderHook(() => useCrisisPersistence());
    expect(result.current.stored).toEqual(noPmtiles);
  });

  it("save() persists the full Crisis object to localStorage and updates stored", () => {
    const { result } = renderHook(() => useCrisisPersistence());
    act(() => {
      result.current.save(fullCrisis);
    });
    expect(result.current.stored).toEqual(fullCrisis);
    expect(JSON.parse(localStorage.getItem(KEY) ?? "null")).toEqual(fullCrisis);
  });

  it("clear() removes the key and resets stored", () => {
    localStorage.setItem(KEY, JSON.stringify(fullCrisis));
    const { result } = renderHook(() => useCrisisPersistence());
    act(() => {
      result.current.clear();
    });
    expect(result.current.stored).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});
