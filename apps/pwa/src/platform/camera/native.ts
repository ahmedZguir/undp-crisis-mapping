// Native camera via @capacitor/camera (dynamically imported). correctOrientation is off so EXIF
// survives for metadata extraction; the canvas re-encode then strips it before upload.

import { extractPhotoMetadata } from "../../lib/photoMetadata";
import type { CameraAdapter, CameraCapture } from "./index";

async function reencodeAsJpeg(blob: Blob): Promise<File> {
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d unavailable");
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();
  return new Promise<File>((resolve, reject) => {
    canvas.toBlob(
      (result) => {
        if (result) resolve(new File([result], "photo.jpeg", { type: "image/jpeg" }));
        else reject(new Error("canvas toBlob returned null"));
      },
      "image/jpeg",
      0.85,
    );
  });
}

async function getPhotoFromSource(
  source: import("@capacitor/camera").CameraSource,
): Promise<CameraCapture | null> {
  const { Camera, CameraResultType } = await import("@capacitor/camera");

  let webPath: string | undefined;
  let format = "jpeg";
  try {
    const photo = await Camera.getPhoto({
      resultType: CameraResultType.Uri,
      source,
      quality: 85,
      correctOrientation: false,
      saveToGallery: false,
    });
    webPath = photo.webPath;
    format = photo.format || "jpeg";
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (!/cancel/i.test(msg)) console.error("[camera] getPhoto failed:", msg);
    return null;
  }
  if (!webPath) return null;

  const resp = await fetch(webPath);
  const original = await resp.blob();
  const originalFile = new File([original], `photo.${format}`, {
    type: original.type || `image/${format}`,
  });

  const metadata = await extractPhotoMetadata(originalFile);

  let blob: File;
  try {
    blob = await reencodeAsJpeg(original);
  } catch {
    blob = originalFile;
  }

  return { blob, metadata };
}

export const nativeCamera: CameraAdapter = {
  async capturePhoto(): Promise<CameraCapture | null> {
    const { CameraSource } = await import("@capacitor/camera");
    return getPhotoFromSource(CameraSource.Camera);
  },
  async pickFromGallery(): Promise<CameraCapture | null> {
    const { CameraSource } = await import("@capacitor/camera");
    return getPhotoFromSource(CameraSource.Photos);
  },
};
