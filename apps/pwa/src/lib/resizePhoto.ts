// Tier-driven photo encoder.

export type PhotoTier = "baseline" | "constrained" | "thumb";
type PhotoFormat = "webp" | "jpeg";

interface TierParams {
  maxEdge: number;
  webpQuality: number;
  jpegQuality: number;
}

export const TIERS: Record<PhotoTier, TierParams> = {
  baseline: { maxEdge: 1600, webpQuality: 0.8, jpegQuality: 0.8 },
  // Slow network at submit time. Quality stays ≥ 0.75 so cracks/rebar/debris survive for AI training.
  constrained: { maxEdge: 1280, webpQuality: 0.78, jpegQuality: 0.75 },
  // Local offline thumb for My Reports; not used for AI training, so quality can be lower.
  thumb: { maxEdge: 320, webpQuality: 0.7, jpegQuality: 0.7 },
};

interface DecodedImage {
  width: number;
  height: number;
  bitmap: ImageBitmap;
}

export interface EncodeOptions {
  format: PhotoFormat;
  quality: number;
}

export interface ResizePhotoDeps {
  decode: (blob: Blob) => Promise<DecodedImage>;
  encode: (
    bitmap: ImageBitmap,
    targetWidth: number,
    targetHeight: number,
    opts: EncodeOptions,
  ) => Promise<Blob>;
}

interface ResizePhotoOptions {
  tier?: PhotoTier;
}

// Falls back to JPEG when convertToBlob silently drops WebP (older Safari, some Android WebViews).
// The returned Blob's `.type` is authoritative.
export async function resizePhoto(
  input: Blob,
  deps: ResizePhotoDeps,
  options: ResizePhotoOptions = {},
): Promise<Blob> {
  const tier = options.tier ?? "baseline";
  const params = TIERS[tier];
  const { width, height, bitmap } = await deps.decode(input);
  const scale =
    Math.max(width, height) > params.maxEdge ? params.maxEdge / Math.max(width, height) : 1;
  const targetWidth = Math.round(width * scale);
  const targetHeight = Math.round(height * scale);

  try {
    const webpOut = await deps.encode(bitmap, targetWidth, targetHeight, {
      format: "webp",
      quality: params.webpQuality,
    });
    if (webpOut.type === "image/webp") return webpOut;

    return await deps.encode(bitmap, targetWidth, targetHeight, {
      format: "jpeg",
      quality: params.jpegQuality,
    });
  } finally {
    // close() is a no-op on the mock bitmaps used in tests.
    bitmap.close?.();
  }
}

// Production deps; jsdom lacks createImageBitmap/OffscreenCanvas, hence the injection seam.
export const defaultDeps: ResizePhotoDeps = {
  async decode(blob) {
    const bitmap = await createImageBitmap(blob);
    return { width: bitmap.width, height: bitmap.height, bitmap };
  },
  async encode(bitmap, w, h, opts) {
    const canvas = new OffscreenCanvas(w, h);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("OffscreenCanvas 2d context unavailable");
    ctx.drawImage(bitmap, 0, 0, w, h);
    const type = opts.format === "webp" ? "image/webp" : "image/jpeg";
    return canvas.convertToBlob({ type, quality: opts.quality });
  },
};
