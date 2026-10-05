// Web camera: a programmatic file input with the rear-camera hint.

import { extractPhotoMetadata } from "../../lib/photoMetadata";
import type { CameraAdapter, CameraCapture } from "./index";

function openFilePicker(useCamera: boolean): Promise<CameraCapture | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    if (useCamera) {
      // Hint the rear camera on mobile browsers; ignored on desktop.
      input.capture = "environment";
    }
    // A detached <input>.click() is a no-op in several browsers/WebViews.
    input.style.display = "none";
    document.body.appendChild(input);
    let settled = false;
    const finish = (value: CameraCapture | null) => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(value);
    };
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) {
        finish(null);
        return;
      }
      const metadata = await extractPhotoMetadata(file);
      finish({ blob: file, metadata });
    };
    // Without this a cancelled picker leaves the promise pending (modern browsers only).
    input.oncancel = () => finish(null);
    input.click();
  });
}

export const webCamera: CameraAdapter = {
  capturePhoto(): Promise<CameraCapture | null> {
    return openFilePicker(true);
  },
  pickFromGallery(): Promise<CameraCapture | null> {
    return openFilePicker(false);
  },
};
