import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { transcribeAudio } from "../api/ai";
import { useLatest } from "../hooks/useLatest";
import { useOnlineStatus } from "../hooks/useOnlineStatus";
import { getClientId } from "../lib/clientId";
import { type RecordingHandle, audioRecorder } from "../platform/audio";

// Voice note -> POST /ai/transcribe; the transcript is appended to the field (clip not stored).

// Mirrors the server-side 60s cap.
const MAX_RECORDING_MS = 60_000;

type RecordState = "idle" | "recording" | "transcribing";

function formatElapsed(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const MAX_LABEL = formatElapsed(MAX_RECORDING_MS / 1000);

function MicIcon({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  );
}

interface VoiceNoteRecorderProps {
  value: string;
  // Receives the merged value, already clamped to `maxLength`.
  onChange: (next: string) => void;
  maxLength: number;
}

export function VoiceNoteRecorder({ value, onChange, maxLength }: VoiceNoteRecorderProps) {
  const { t } = useTranslation();

  const online = useOnlineStatus();
  const canRecord = online && audioRecorder.isSupported();
  const [recState, setRecState] = useState<RecordState>("idle");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [voiceError, setVoiceError] = useState(false);

  const handleRef = useRef<RecordingHandle | null>(null);
  const tickRef = useRef<number | null>(null);
  const autoStopRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  // start() awaits the mic prompt; the resolved start bails if this was cleared meanwhile.
  const wantRecordingRef = useRef(false);
  // Read at transcription time so a slow round-trip appends to current text.
  const valueRef = useLatest(value);

  const clearTimers = useCallback(() => {
    if (tickRef.current !== null) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
    if (autoStopRef.current !== null) {
      clearTimeout(autoStopRef.current);
      autoStopRef.current = null;
    }
  }, []);

  const applyTranscript = useCallback(
    (text: string) => {
      const existing = valueRef.current.trim();
      const merged = existing ? `${existing} ${text}` : text;
      onChange(merged.slice(0, maxLength));
    },
    [onChange, maxLength],
  );

  const stopRecording = useCallback(async () => {
    wantRecordingRef.current = false;
    clearTimers();
    const handle = handleRef.current;
    handleRef.current = null;
    if (!handle) return;
    setRecState("transcribing");
    let blob: Blob;
    try {
      ({ blob } = await handle.stop());
    } catch {
      setVoiceError(true);
      setRecState("idle");
      return;
    }
    let clientId: string;
    try {
      clientId = await getClientId();
    } catch {
      setVoiceError(true);
      setRecState("idle");
      return;
    }
    const text = await transcribeAudio(blob, clientId);
    if (text === null) {
      setVoiceError(true);
    } else {
      applyTranscript(text);
    }
    setRecState("idle");
  }, [clearTimers, applyTranscript]);

  const startRecording = useCallback(async () => {
    setVoiceError(false);
    // Set before the async permission round-trip so a stop/unmount in between
    // prevents a dangling recording.
    wantRecordingRef.current = true;
    let handle: RecordingHandle;
    try {
      handle = await audioRecorder.start();
    } catch {
      wantRecordingRef.current = false;
      setVoiceError(true);
      return;
    }
    if (!wantRecordingRef.current) {
      handle.cancel();
      return;
    }
    handleRef.current = handle;
    startedAtRef.current = Date.now();
    setElapsedSec(0);
    setRecState("recording");
    tickRef.current = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 250);
    autoStopRef.current = window.setTimeout(() => {
      void stopRecording();
    }, MAX_RECORDING_MS);
  }, [stopRecording]);

  useEffect(() => {
    return () => {
      clearTimers();
      wantRecordingRef.current = false;
      handleRef.current?.cancel();
      handleRef.current = null;
    };
  }, [clearTimers]);

  if (!canRecord) return null;

  const isRecording = recState === "recording";
  const isTranscribing = recState === "transcribing";
  const active = isRecording || isTranscribing;
  const btnSize = "clamp(34px, 9vw, 40px)";

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, maxWidth: "100%" }}>
      <button
        type="button"
        onClick={() => void startRecording()}
        disabled={active}
        aria-label={t("voiceNote.recordStart")}
        style={{
          width: btnSize,
          height: btnSize,
          flexShrink: 0,
          display: "grid",
          placeItems: "center",
          padding: 0,
          borderRadius: 999,
          border: "1.5px solid var(--c-blue-200, var(--c-line))",
          background: "var(--c-card)",
          color: "var(--c-blue-700)",
          cursor: active ? "default" : "pointer",
          opacity: active ? 0.5 : 1,
          boxShadow: "0 1px 2px rgba(0,0,0,0.12)",
        }}
      >
        <MicIcon size={20} />
      </button>
      {recState === "idle" && voiceError && (
        <span
          role="alert"
          style={{
            fontSize: 11,
            color: "var(--c-danger)",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {t("voiceNote.voiceError")}
        </span>
      )}

      {/* Portaled to <body>: transformed step-transition ancestors break fixed positioning. */}
      {active &&
        typeof document !== "undefined" &&
        createPortal(
          <dialog
            open
            aria-modal="true"
            aria-label={isRecording ? t("voiceNote.recording") : t("voiceNote.transcribing")}
            style={{
              position: "fixed",
              inset: 0,
              border: 0,
              margin: 0,
              maxWidth: "100vw",
              maxHeight: "100vh",
              width: "100vw",
              height: "100vh",
              zIndex: 3000,
              background: "rgba(8,16,32,0.55)",
              display: "grid",
              placeItems: "center",
              padding: 24,
            }}
          >
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 18,
              }}
            >
              <div
                style={{
                  position: "relative",
                  display: "grid",
                  placeItems: "center",
                  width: 132,
                  height: 132,
                }}
              >
                {isRecording && (
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      inset: 0,
                      borderRadius: 999,
                      border: "3px solid var(--c-danger)",
                      animation: "asrRingPulse 1.4s ease-out infinite",
                    }}
                  />
                )}
                <button
                  type="button"
                  onClick={isRecording ? () => void stopRecording() : undefined}
                  disabled={isTranscribing}
                  aria-label={isRecording ? t("voiceNote.recordStop") : t("voiceNote.transcribing")}
                  style={{
                    width: 96,
                    height: 96,
                    borderRadius: 999,
                    border: "none",
                    background: isRecording ? "var(--c-danger)" : "var(--c-card)",
                    color: isRecording ? "#fff" : "var(--c-blue-700)",
                    display: "grid",
                    placeItems: "center",
                    cursor: isRecording ? "pointer" : "default",
                    boxShadow: "0 8px 24px rgba(0,0,0,0.28)",
                    animation: isRecording ? "asrBtnPulse 1.6s ease-in-out infinite" : "none",
                  }}
                >
                  {isTranscribing ? (
                    <span
                      aria-hidden="true"
                      style={{
                        width: 34,
                        height: 34,
                        borderRadius: "50%",
                        border: "3px solid var(--c-line)",
                        borderTopColor: "var(--c-blue-700)",
                        animation: "spin 0.7s linear infinite",
                      }}
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      style={{ width: 30, height: 30, borderRadius: 8, background: "#fff" }}
                    />
                  )}
                </button>
              </div>
              <div aria-live="polite" style={{ textAlign: "center", color: "#fff" }}>
                {isRecording ? (
                  <>
                    <div
                      style={{
                        fontSize: 20,
                        fontWeight: 800,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {formatElapsed(elapsedSec)}{" "}
                      <span style={{ opacity: 0.6, fontWeight: 600, fontSize: 14 }}>
                        / {MAX_LABEL}
                      </span>
                    </div>
                    <div style={{ marginTop: 6, fontSize: 13, opacity: 0.85 }}>
                      {t("voiceNote.tapToStop", { defaultValue: "Tap to stop" })}
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{t("voiceNote.transcribing")}</div>
                )}
              </div>
            </div>
          </dialog>,
          document.body,
        )}

      <style>{`
          @keyframes asrBtnPulse { 0%,100%{transform:scale(.95);} 50%{transform:scale(1);} }
          @keyframes asrRingPulse { 0%{opacity:.7;transform:scale(1);} 100%{opacity:0;transform:scale(1.5);} }
        `}</style>
    </div>
  );
}
