import { act, renderHook, waitFor } from "@testing-library/react";
import { deleteDB } from "idb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetForTests } from "../lib/db";
import type { FormState } from "../types";
import { useDrafts } from "./useDrafts";

function emptyForm(overrides: Partial<FormState> = {}): FormState {
  return {
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
    latitude: null,
    longitude: null,
    route_description: "",
    photo_metadata: null,
    generic_answers: {},
    ...overrides,
  };
}

beforeEach(async () => {
  await _resetForTests();
  await deleteDB("undp-app");
  await deleteDB("keyval-store");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDrafts", () => {
  it("starts empty", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    expect(result.current.drafts).toEqual([]);
  });

  it("does not persist a draft until something material is selected (gate)", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    const id = result.current.newDraftId();
    act(() => {
      result.current.save(id, emptyForm({ infra_type_other: "typing" }), 0);
    });
    // Wait past debounce.
    await new Promise((r) => setTimeout(r, 500));
    await waitFor(() => expect(result.current.drafts.length).toBe(0));
  });

  it("persists the draft once a material field is set", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    const id = result.current.newDraftId();
    act(() => {
      result.current.save(id, emptyForm({ infra_name: "Old market" }), 1);
    });
    await new Promise((r) => setTimeout(r, 500));
    await waitFor(() => expect(result.current.drafts.length).toBe(1));
    expect(result.current.drafts[0].state.infra_name).toBe("Old market");
    expect(result.current.drafts[0].step).toBe(1);
  });

  it("supports multiple independent drafts", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    const id1 = result.current.newDraftId();
    const id2 = result.current.newDraftId();
    act(() => {
      result.current.save(id1, emptyForm({ infra_name: "A" }), 0);
      result.current.save(id2, emptyForm({ infra_name: "B" }), 0);
    });
    await new Promise((r) => setTimeout(r, 500));
    await waitFor(() => expect(result.current.drafts.length).toBe(2));
    const names = result.current.drafts.map((d) => d.state.infra_name).sort();
    expect(names).toEqual(["A", "B"]);
  });

  it("discard removes only the targeted draft", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    const id1 = result.current.newDraftId();
    const id2 = result.current.newDraftId();
    act(() => {
      result.current.save(id1, emptyForm({ infra_name: "keep" }), 0);
      result.current.save(id2, emptyForm({ infra_name: "drop" }), 0);
    });
    await new Promise((r) => setTimeout(r, 500));
    await waitFor(() => expect(result.current.drafts.length).toBe(2));

    await act(async () => {
      await result.current.discard(id2);
    });
    await waitFor(() => expect(result.current.drafts.length).toBe(1));
    expect(result.current.drafts[0].state.infra_name).toBe("keep");
  });

  it("debounces saves so only the latest state is persisted", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    const id = result.current.newDraftId();
    act(() => {
      result.current.save(id, emptyForm({ infra_name: "first" }), 0);
      result.current.save(id, emptyForm({ infra_name: "second" }), 1);
      result.current.save(id, emptyForm({ infra_name: "final" }), 2);
    });
    await new Promise((r) => setTimeout(r, 500));
    await waitFor(() => expect(result.current.drafts.length).toBe(1));
    expect(result.current.drafts[0].state.infra_name).toBe("final");
    expect(result.current.drafts[0].step).toBe(2);
  });

  it("load() returns the saved state and step", async () => {
    const { result } = renderHook(() => useDrafts());
    await waitFor(() => expect(result.current.drafts).toHaveLength(0));
    const id = result.current.newDraftId();
    act(() => {
      result.current.save(id, emptyForm({ infra_name: "Load me", damage_class: "partial" }), 4);
    });
    await new Promise((r) => setTimeout(r, 500));

    const loaded = await result.current.load(id);
    expect(loaded?.state.infra_name).toBe("Load me");
    expect(loaded?.state.damage_class).toBe("partial");
    expect(loaded?.step).toBe(4);
  });
});
