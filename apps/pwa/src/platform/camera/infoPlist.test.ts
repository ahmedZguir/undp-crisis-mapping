import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// App Review rejects purpose strings that misdescribe use, and StepPhoto offers pickFromGallery.
const plist = readFileSync(path.resolve(process.cwd(), "ios/App/App/Info.plist"), "utf8");
const native = readFileSync(path.resolve(process.cwd(), "src/platform/camera/native.ts"), "utf8");

function purpose(key: string): string {
  const m = plist.match(new RegExp(`<key>${key}</key>\\s*<string>([^<]*)</string>`));
  if (!m) throw new Error(`${key} missing from Info.plist`);
  return m[1];
}

describe("iOS photo-library purpose strings", () => {
  it("don't deny reading the library while the gallery picker exists", () => {
    expect(native).toContain("CameraSource.Photos");
    for (const key of ["NSPhotoLibraryUsageDescription", "NSPhotoLibraryAddUsageDescription"]) {
      expect(purpose(key)).not.toMatch(/does not (read|browse)/i);
    }
  });
});
