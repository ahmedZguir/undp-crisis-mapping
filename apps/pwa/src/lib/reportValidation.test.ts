import { describe, expect, it } from "vitest";
import type { FormState } from "../types";
import { reportContentStatus } from "./reportValidation";

// A FormState with nothing filled in — the starting point of a fresh report.
function emptyState(): FormState {
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
    crisis_id: "crisis-1",
    latitude: null,
    longitude: null,
    route_description: "",
    photo_metadata: null,
    generic_answers: {},
  };
}

// A photo stand-in — the gate only checks for presence, never reads bytes.
const fakePhoto = new File([], "photo.jpg", { type: "image/jpeg" }) as File;

describe("reportContentStatus", () => {
  it("blocks an empty report", () => {
    const s = reportContentStatus(emptyState());
    expect(s.submittable).toBe(false);
    expect(s.hasPhotoOrDescription).toBe(false);
    expect(s.hasLocationOrRoute).toBe(false);
    expect(s.hasDamageClass).toBe(false);
  });

  it("accepts a photo + GPS + damage report", () => {
    const s = reportContentStatus({
      ...emptyState(),
      photo: fakePhoto,
      latitude: 25.3,
      longitude: 51.5,
      damage_class: "partial",
    });
    expect(s.submittable).toBe(true);
  });

  it("accepts a description + route-description report with no photo and no GPS", () => {
    const s = reportContentStatus({
      ...emptyState(),
      description: "Collapsed wall on the north side",
      route_description: "Behind the old market, second alley",
      damage_class: "complete",
    });
    expect(s.hasPhotoOrDescription).toBe(true);
    expect(s.hasLocationOrRoute).toBe(true);
    expect(s.submittable).toBe(true);
  });

  it("treats a picked building (building_id) as a location", () => {
    const s = reportContentStatus({
      ...emptyState(),
      photo: fakePhoto,
      building_id: "08f2c...gers",
      damage_class: "minimal",
    });
    expect(s.hasLocationOrRoute).toBe(true);
    expect(s.submittable).toBe(true);
  });

  it("treats whitespace-only text as absent", () => {
    const s = reportContentStatus({
      ...emptyState(),
      description: "   ",
      route_description: "  ",
      damage_class: "minimal",
    });
    expect(s.hasPhotoOrDescription).toBe(false);
    expect(s.hasLocationOrRoute).toBe(false);
    expect(s.submittable).toBe(false);
  });

  it("blocks when damage_class is missing even if both content pairs are satisfied", () => {
    const s = reportContentStatus({
      ...emptyState(),
      photo: fakePhoto,
      latitude: 25.3,
      longitude: 51.5,
      damage_class: null,
    });
    expect(s.hasPhotoOrDescription).toBe(true);
    expect(s.hasLocationOrRoute).toBe(true);
    expect(s.hasDamageClass).toBe(false);
    expect(s.submittable).toBe(false);
  });

  it("requires both a content signal AND a location signal, not just one", () => {
    // photo + damage but no location/route → still blocked.
    const noLocation = reportContentStatus({
      ...emptyState(),
      photo: fakePhoto,
      damage_class: "minimal",
    });
    expect(noLocation.submittable).toBe(false);

    // location + damage but no photo/description → still blocked.
    const noContent = reportContentStatus({
      ...emptyState(),
      latitude: 25.3,
      longitude: 51.5,
      damage_class: "minimal",
    });
    expect(noContent.submittable).toBe(false);
  });
});
