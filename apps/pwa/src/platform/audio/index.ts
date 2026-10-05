// Voice-note recorder adapter (web MediaRecorder / native voice-recorder plugin). Clips are uploaded
// for transcription and never stored.

import { isNativePlatform } from "../platformInfo";
import { nativeAudioRecorder } from "./native";
import { webAudioRecorder } from "./web";

// The caller enforces the 60s cap (mirrored server-side).
export interface AudioRecording {
  // `type` is browser-chosen (e.g. webm/opus on Chrome, mp4 on Safari).
  blob: Blob;
}

export interface RecordingHandle {
  stop(): Promise<AudioRecording>;
  cancel(): void;
}

export interface AudioRecorderAdapter {
  isSupported(): boolean;
  // Rejects if mic permission is denied or unavailable.
  start(): Promise<RecordingHandle>;
}

export const audioRecorder: AudioRecorderAdapter = isNativePlatform()
  ? nativeAudioRecorder
  : webAudioRecorder;
