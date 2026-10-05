import { describe, expect, it, vi } from "vitest";
import {
  type EncodeOptions,
  type PhotoTier,
  type ResizePhotoDeps,
  TIERS,
  resizePhoto,
} from "./resizePhoto";

interface DepsConfig {
  srcWidth: number;
  srcHeight: number;
  // When set, the WebP encode call returns this MIME type instead of
  // image/webp, simulating the silent-fallback case from the spec.
  webpReturnsType?: string;
}

function makeDeps(cfg: DepsConfig): { deps: ResizePhotoDeps; encodeCalls: EncodeOptions[] } {
  const encodeCalls: EncodeOptions[] = [];
  const deps: ResizePhotoDeps = {
    async decode() {
      return {
        width: cfg.srcWidth,
        height: cfg.srcHeight,
        bitmap: { close: vi.fn() } as unknown as ImageBitmap,
      };
    },
    async encode(_bitmap, w, h, opts) {
      encodeCalls.push(opts);
      const type = opts.format === "webp" ? (cfg.webpReturnsType ?? "image/webp") : "image/jpeg";
      const payload = JSON.stringify({ w, h, q: opts.quality });
      return new Blob([payload], { type });
    },
  };
  return { deps, encodeCalls };
}

async function readDims(blob: Blob): Promise<{ w: number; h: number; q: number }> {
  return JSON.parse(await blob.text());
}

describe("resizePhoto", () => {
  it("defaults to baseline tier and emits image/webp when supported", async () => {
    const { deps, encodeCalls } = makeDeps({ srcWidth: 3200, srcHeight: 2400 });
    const out = await resizePhoto(new Blob(["src"], { type: "image/png" }), deps);

    expect(out.type).toBe("image/webp");
    expect(encodeCalls).toHaveLength(1);
    expect(encodeCalls[0].format).toBe("webp");
    expect(encodeCalls[0].quality).toBe(TIERS.baseline.webpQuality);

    const { w, h, q } = await readDims(out);
    expect(w).toBe(1600);
    expect(h).toBe(1200);
    expect(q).toBe(TIERS.baseline.webpQuality);
  });

  it("scales by the longer edge regardless of orientation", async () => {
    const { deps } = makeDeps({ srcWidth: 2400, srcHeight: 3200 });
    const out = await resizePhoto(new Blob(["src"], { type: "image/png" }), deps);
    const { w, h } = await readDims(out);
    expect(h).toBe(1600);
    expect(w).toBe(1200);
  });

  it("does not upscale: source smaller than tier edge keeps its dimensions", async () => {
    const { deps } = makeDeps({ srcWidth: 800, srcHeight: 600 });
    const out = await resizePhoto(new Blob(["src"], { type: "image/png" }), deps);
    const { w, h } = await readDims(out);
    expect(w).toBe(800);
    expect(h).toBe(600);
  });

  it("constrained tier uses 1280px / q=0.78 webp", async () => {
    const { deps, encodeCalls } = makeDeps({ srcWidth: 3200, srcHeight: 2400 });
    const out = await resizePhoto(new Blob(["src"], { type: "image/jpeg" }), deps, {
      tier: "constrained",
    });
    expect(out.type).toBe("image/webp");
    expect(encodeCalls[0].quality).toBe(TIERS.constrained.webpQuality);
    const { w, h } = await readDims(out);
    expect(w).toBe(1280);
    expect(h).toBe(960);
  });

  it.each<PhotoTier>(["baseline", "constrained"])(
    "falls back to JPEG at the tier's jpegQuality when WebP encode silently returns PNG (%s)",
    async (tier) => {
      const { deps, encodeCalls } = makeDeps({
        srcWidth: 1200,
        srcHeight: 900,
        webpReturnsType: "image/png",
      });
      const out = await resizePhoto(new Blob(["src"], { type: "image/jpeg" }), deps, {
        tier,
      });
      expect(out.type).toBe("image/jpeg");
      expect(encodeCalls).toHaveLength(2);
      expect(encodeCalls[0].format).toBe("webp");
      expect(encodeCalls[1].format).toBe("jpeg");
      expect(encodeCalls[1].quality).toBe(TIERS[tier].jpegQuality);
    },
  );

  it("is pure: same input yields equivalent output", async () => {
    const { deps: d1 } = makeDeps({ srcWidth: 2000, srcHeight: 1000 });
    const { deps: d2 } = makeDeps({ srcWidth: 2000, srcHeight: 1000 });
    const a = await resizePhoto(new Blob(["src"], { type: "image/jpeg" }), d1);
    const b = await resizePhoto(new Blob(["src"], { type: "image/jpeg" }), d2);
    expect(a.type).toBe(b.type);
    expect(await a.text()).toBe(await b.text());
  });
});
