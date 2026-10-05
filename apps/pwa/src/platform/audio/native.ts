// Native recorder via `@independo/capacitor-voice-recorder`, an SPM-compatible fork (the original
// ships no Package.swift). Dynamically imported to stay out of the web bundle.

import { base64ToBytes } from "../../lib/base64";
import type { AudioRecorderAdapter, AudioRecording, RecordingHandle } from "./index";

// Only for a missing type; the server sniffs the real container (apps/api/src/api/reports/audio.py).
const FALLBACK_MIME = "audio/aac";

function base64ToBlob(base64: string, type: string): Blob {
  return new Blob([base64ToBytes(base64)], { type });
}

export const nativeAudioRecorder: AudioRecorderAdapter = {
  // The real gate is mic permission: a denial surfaces as a rejected `start()`.
  isSupported(): boolean {
    return true;
  },

  async start(): Promise<RecordingHandle> {
    const { VoiceRecorder } = await import("@independo/capacitor-voice-recorder");

    const { value: granted } = await VoiceRecorder.hasAudioRecordingPermission().catch(() => ({
      value: false,
    }));
    if (!granted) {
      const requested = await VoiceRecorder.requestAudioRecordingPermission();
      if (!requested.value) throw new Error("microphone permission denied");
    }

    await VoiceRecorder.startRecording();

    let settled = false;
    return {
      async stop(): Promise<AudioRecording> {
        settled = true;
        const { value } = await VoiceRecorder.stopRecording();
        // Recorded to memory, so the clip is base64; missing means nothing was captured.
        if (!value.recordDataBase64) throw new Error("empty recording");
        return { blob: base64ToBlob(value.recordDataBase64, value.mimeType || FALLBACK_MIME) };
      },
      cancel(): void {
        if (settled) return;
        settled = true;
        void VoiceRecorder.stopRecording().catch(() => {});
      },
    };
  },
};
