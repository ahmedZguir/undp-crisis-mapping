import { describe, expect, it, vi } from "vitest";

vi.mock("exifr", () => ({
  default: {
    parse: vi.fn(),
  },
}));

import exifr from "exifr";
import { extractPhotoMetadata, metersBetween } from "./photoMetadata";

const parseMock = exifr.parse as unknown as ReturnType<typeof vi.fn>;

function makeFile(): Blob {
  return new Blob(["fake-jpeg"], { type: "image/jpeg" });
}

describe("extractPhotoMetadata", () => {
  it("returns parsed GPS, datetime, orientation, dims, and camera", async () => {
    parseMock.mockResolvedValueOnce({
      latitude: 25.2854,
      longitude: 51.531,
      GPSHPositioningError: 12,
      DateTimeOriginal: new Date("2026-05-14T10:00:00Z"),
      Orientation: 6,
      ExifImageWidth: 4032,
      ExifImageHeight: 3024,
      Make: "Apple",
      Model: "iPhone 15",
      Software: "iOS 17.4",
    });

    const meta = await extractPhotoMetadata(makeFile());

    expect(meta.gps).toEqual({ latitude: 25.2854, longitude: 51.531, accuracy: 12 });
    expect(meta.capturedAt).toBe("2026-05-14T10:00:00.000Z");
    expect(meta.orientation).toBe(6);
    expect(meta.width).toBe(4032);
    expect(meta.height).toBe(3024);
    expect(meta.camera).toEqual({ make: "Apple", model: "iPhone 15", software: "iOS 17.4" });
    expect(typeof meta.extractedAt).toBe("string");
  });

  it("returns nulls when EXIF is missing GPS but present otherwise", async () => {
    parseMock.mockResolvedValueOnce({
      DateTimeOriginal: "2026-01-02T03:04:05Z",
      Orientation: 1,
    });

    const meta = await extractPhotoMetadata(makeFile());
    expect(meta.gps).toBeNull();
    expect(meta.capturedAt).toBe("2026-01-02T03:04:05.000Z");
    expect(meta.orientation).toBe(1);
    expect(meta.camera).toBeNull();
  });

  it("returns all-null payload when exifr returns undefined", async () => {
    parseMock.mockResolvedValueOnce(undefined);
    const meta = await extractPhotoMetadata(makeFile());
    expect(meta.gps).toBeNull();
    expect(meta.capturedAt).toBeNull();
    expect(meta.orientation).toBeNull();
    expect(meta.width).toBeNull();
    expect(meta.height).toBeNull();
    expect(meta.camera).toBeNull();
  });

  it("never throws on parse failure", async () => {
    parseMock.mockRejectedValueOnce(new Error("boom"));
    const meta = await extractPhotoMetadata(makeFile());
    expect(meta.gps).toBeNull();
    expect(meta.capturedAt).toBeNull();
  });
});

describe("metersBetween", () => {
  it("returns 0 for identical points", () => {
    const p = { latitude: 25.2854, longitude: 51.531 };
    expect(metersBetween(p, p)).toBe(0);
  });

  it("approximates Doha → Riyadh (~493 km great-circle)", () => {
    const doha = { latitude: 25.2854, longitude: 51.531 };
    const riyadh = { latitude: 24.7136, longitude: 46.6753 };
    const m = metersBetween(doha, riyadh);
    expect(m).toBeGreaterThan(485_000);
    expect(m).toBeLessThan(500_000);
  });

  it("detects sub-100m differences", () => {
    const a = { latitude: 25.2854, longitude: 51.531 };
    const b = { latitude: 25.286, longitude: 51.531 };
    const m = metersBetween(a, b);
    expect(m).toBeGreaterThan(50);
    expect(m).toBeLessThan(100);
  });
});
