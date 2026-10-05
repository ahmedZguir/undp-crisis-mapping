import { useCallback, useEffect, useRef, useState } from "react";

interface PhotoEditorProps {
  file: File;
  onCancel: () => void;
  onApply: (edited: File) => void;
}

interface Pan {
  x: number;
  y: number;
}

interface PinchState {
  startDist: number;
  startZoom: number;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 6;
// Crop frame side as a percentage of the stage's shorter side.
const CROP_PERCENT = 86;
const OUTPUT_SIZE_PX = 1280;
const JPEG_QUALITY = 0.9;

const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

// Square crop over a pannable/zoomable image; exports the pixels under the frame as JPEG.
export function PhotoEditor({ file, onCancel, onApply }: PhotoEditorProps) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Pan>({ x: 0, y: 0 });
  const [busy, setBusy] = useState(false);

  const stageRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; pan: Pan } | null>(null);
  const pinchRef = useRef<PinchState | null>(null);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setImageUrl(url);
    const i = new Image();
    i.onload = () => setImg(i);
    i.src = url;
    setZoom(1);
    setPan({ x: 0, y: 0 });
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      (e.target as Element).setPointerCapture?.(e.pointerId);
      dragRef.current = { x: e.clientX, y: e.clientY, pan };
    },
    [pan],
  );

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    setPan({ x: d.pan.x + (e.clientX - d.x), y: d.pan.y + (e.clientY - d.y) });
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    dragRef.current = null;
  }, []);

  // Touch events for two-finger pinch, pointer events for drag.
  const onTouchStart = useCallback(
    (e: React.TouchEvent<HTMLDivElement>) => {
      if (e.touches.length !== 2) return;
      const a = e.touches[0];
      const b = e.touches[1];
      const dx = a.clientX - b.clientX;
      const dy = a.clientY - b.clientY;
      pinchRef.current = {
        startDist: Math.hypot(dx, dy),
        startZoom: zoom,
      };
      dragRef.current = null;
    },
    [zoom],
  );

  const onTouchMove = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    const p = pinchRef.current;
    if (!p || e.touches.length !== 2) return;
    e.preventDefault();
    const a = e.touches[0];
    const b = e.touches[1];
    const dx = a.clientX - b.clientX;
    const dy = a.clientY - b.clientY;
    const dist = Math.hypot(dx, dy);
    const nextZoom = clampZoom((dist / p.startDist) * p.startZoom);
    setZoom(nextZoom);
  }, []);

  const onTouchEnd = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    if (e.touches.length < 2) pinchRef.current = null;
  }, []);

  const onWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    e.preventDefault();
    const delta = -e.deltaY * 0.0015;
    setZoom((z) => clampZoom(z + delta * z));
  }, []);

  const apply = useCallback(async () => {
    if (!img || !stageRef.current) return;
    setBusy(true);
    try {
      const stage = stageRef.current;
      const stageRect = stage.getBoundingClientRect();
      const stageSize = Math.min(stageRect.width, stageRect.height);
      const cropSize = stageSize * (CROP_PERCENT / 100); // matches the visual frame below
      const cropOriginX = (stageRect.width - cropSize) / 2;
      const cropOriginY = (stageRect.height - cropSize) / 2;

      // Image is object-fit: contain, then scaled by zoom and translated by pan.
      const baseScale = Math.min(
        stageRect.width / img.naturalWidth,
        stageRect.height / img.naturalHeight,
      );
      const renderedW = img.naturalWidth * baseScale * zoom;
      const renderedH = img.naturalHeight * baseScale * zoom;
      const renderedX = (stageRect.width - renderedW) / 2 + pan.x;
      const renderedY = (stageRect.height - renderedH) / 2 + pan.y;

      const pxPerSourceX = renderedW / img.naturalWidth;
      const pxPerSourceY = renderedH / img.naturalHeight;
      const srcX = (cropOriginX - renderedX) / pxPerSourceX;
      const srcY = (cropOriginY - renderedY) / pxPerSourceY;
      const srcW = cropSize / pxPerSourceX;
      const srcH = cropSize / pxPerSourceY;

      const clampedX = Math.max(0, Math.min(img.naturalWidth, srcX));
      const clampedY = Math.max(0, Math.min(img.naturalHeight, srcY));
      const clampedW = Math.max(1, Math.min(img.naturalWidth - clampedX, srcW));
      const clampedH = Math.max(1, Math.min(img.naturalHeight - clampedY, srcH));

      const outSize = OUTPUT_SIZE_PX;
      const canvas = document.createElement("canvas");
      canvas.width = outSize;
      canvas.height = outSize;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("2d context unavailable");
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, clampedX, clampedY, clampedW, clampedH, 0, 0, outSize, outSize);
      const blob: Blob | null = await new Promise((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/jpeg", JPEG_QUALITY),
      );
      if (!blob) throw new Error("crop failed");
      const name = file.name.replace(/\.\w+$/, ".jpg");
      const edited = new File([blob], name, { type: "image/jpeg", lastModified: Date.now() });
      onApply(edited);
    } finally {
      setBusy(false);
    }
  }, [img, zoom, pan, file, onApply]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "#000",
        zIndex: 1700,
        display: "flex",
        flexDirection: "column",
        color: "#fff",
        fontFamily: "var(--font-ui)",
      }}
    >
      <div
        style={{
          padding: "max(env(safe-area-inset-top, 12px), 12px) 16px 12px",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel edit"
          style={{
            background: "rgba(255,255,255,0.12)",
            color: "#fff",
            border: 0,
            padding: "8px 12px",
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Cancel
        </button>
        <div style={{ flex: 1, textAlign: "center", fontSize: 14, fontWeight: 700 }}>
          Crop & zoom
        </div>
        <button
          type="button"
          onClick={() => void apply()}
          disabled={!img || busy}
          style={{
            background: "var(--c-blue-700, #1d4ed8)",
            color: "#fff",
            border: 0,
            padding: "8px 14px",
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 700,
            cursor: !img || busy ? "not-allowed" : "pointer",
            opacity: !img || busy ? 0.6 : 1,
          }}
        >
          {busy ? "Saving…" : "Done"}
        </button>
      </div>

      <div
        ref={stageRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onWheel={onWheel}
        style={{
          flex: 1,
          position: "relative",
          overflow: "hidden",
          touchAction: "none",
        }}
      >
        {imageUrl && (
          <img
            src={imageUrl}
            alt=""
            draggable={false}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              objectFit: "contain",
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: "center center",
              userSelect: "none",
              pointerEvents: "none",
            }}
          />
        )}
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            display: "grid",
            placeItems: "center",
          }}
        >
          <div
            style={{
              width: `${CROP_PERCENT}%`,
              aspectRatio: "1 / 1",
              boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)",
              border: "2px solid #fff",
              borderRadius: 4,
            }}
          />
        </div>
      </div>

      <div
        style={{
          padding: "12px 16px calc(20px + env(safe-area-inset-bottom)) 16px",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <span aria-hidden="true" style={{ fontSize: 12 }}>
          −
        </span>
        <input
          aria-label="Zoom"
          type="range"
          min={MIN_ZOOM}
          max={MAX_ZOOM}
          step={0.01}
          value={zoom}
          onChange={(e) => setZoom(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <span aria-hidden="true" style={{ fontSize: 16 }}>
          +
        </span>
      </div>
    </div>
  );
}
