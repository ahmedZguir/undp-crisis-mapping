// Web voice recorder via MediaRecorder; nothing is persisted.

import type { AudioRecorderAdapter, AudioRecording, RecordingHandle } from "./index";

// Only a nudge: the server transcodes whatever arrives; "" lets the browser choose.
const PREFERRED_MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];

function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
  return PREFERRED_MIME_TYPES.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
}

export const webAudioRecorder: AudioRecorderAdapter = {
  isSupported(): boolean {
    return (
      typeof navigator !== "undefined" &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== "undefined"
    );
  },

  async start(): Promise<RecordingHandle> {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.start();

    const releaseMic = () => {
      for (const track of stream.getTracks()) track.stop();
    };

    return {
      stop(): Promise<AudioRecording> {
        return new Promise<AudioRecording>((resolve) => {
          recorder.onstop = () => {
            releaseMic();
            const type = recorder.mimeType || mimeType || "audio/webm";
            resolve({ blob: new Blob(chunks, { type }) });
          };
          // `inactive` if it never really started (e.g. immediate stop).
          if (recorder.state === "inactive") {
            recorder.onstop?.(new Event("stop"));
          } else {
            recorder.stop();
          }
        });
      },
      cancel(): void {
        try {
          if (recorder.state !== "inactive") recorder.stop();
        } catch {
          // already stopped
        }
        releaseMic();
      },
    };
  },
};
