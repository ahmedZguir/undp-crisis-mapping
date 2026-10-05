// Report-photo capture adapter. GPS comes from the photo's EXIF on both platforms.

import type { PhotoMetadata } from "../../lib/photoMetadata";
import { isNativePlatform } from "../platformInfo";
import { nativeCamera } from "./native";
import { webCamera } from "./web";

export interface CameraCapture {
  blob: File;
  metadata: PhotoMetadata;
}

export interface CameraAdapter {
  capturePhoto(): Promise<CameraCapture | null>;
  pickFromGallery(): Promise<CameraCapture | null>;
}

export const camera: CameraAdapter = isNativePlatform() ? nativeCamera : webCamera;
