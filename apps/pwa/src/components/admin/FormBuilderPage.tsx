// Per-crisis form builder: a horizontal track of phone previews of each
// citizen step. Generic pages are inline-editable; built-in pages are
// read-only previews.
//
// Reordering is HTML5 drag with live reflow: the page moves as the cursor
// crosses neighbours. This relies on the stable `pageId` keys from
// useFormBuilderState; positional keys would unmount the dragged node and
// cancel the drag.
//
// Below ~900px a "use a desktop browser" notice replaces the builder.
import { Fragment, useEffect, useRef, useState } from "react";
import { type AdminFormResponse, getForm, publishForm } from "../../api/adminForms";
import type { FormSchema } from "../../types";
import { PagePhone, ReviewPhone } from "./formBuilder/PagePhone";
import { useFormBuilderState } from "./formBuilder/useFormBuilderState";

interface FormBuilderPageProps {
  crisisId?: string;
  // Embedded mode (before the crisis exists): the edited schema goes back via
  // onSave instead of being published.
  initialSchema?: FormSchema;
  onSave?: (schema: FormSchema) => void;
  // Embedded mode only: "Back to crisis" without saving.
  onCancel?: () => void;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

export function FormBuilderPage({
  crisisId,
  initialSchema,
  onSave,
  onCancel,
}: FormBuilderPageProps = {}) {
  const embedded = onSave !== undefined;
  const { state, dispatch } = useFormBuilderState(embedded ? initialSchema : undefined);
  const [status, setStatus] = useState<Status>(embedded ? { kind: "ready" } : { kind: "idle" });
  const [publishing, setPublishing] = useState(false);
  const [validationErrors, setValidationErrors] = useState<
    Array<{ path: string; message: string }>
  >([]);
  const [conflict, setConflict] = useState<AdminFormResponse | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [isNarrow, setIsNarrow] = useState(() =>
    typeof window === "undefined" ? false : window.innerWidth < 900,
  );

  // Live position of the dragged page; updated as the reducer reorders mid-drag.
  const [draggingPageIdx, setDraggingPageIdx] = useState<number | null>(null);
  // At most one reorder per frame; onDragOver can fire many times per frame.
  const lastReorderRef = useRef<{ from: number; to: number } | null>(null);
  // Grab the empty background to pan the track horizontally.
  const trackScrollRef = useRef<HTMLDivElement | null>(null);
  const panStateRef = useRef<{ startX: number; startScroll: number } | null>(null);
  const [panning, setPanning] = useState(false);

  function isInteractive(el: Element | null): boolean {
    if (!el) return false;
    return !!el.closest("button, input, textarea, select, label, [draggable='true'], a");
  }

  function handleTrackPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    // Left button on the background only; never hijack clicks on controls.
    if (e.button !== 0) return;
    if (isInteractive(e.target as Element)) return;
    const container = trackScrollRef.current;
    if (!container) return;
    panStateRef.current = { startX: e.clientX, startScroll: container.scrollLeft };
    setPanning(true);
    container.setPointerCapture(e.pointerId);
  }

  function handleTrackPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const pan = panStateRef.current;
    const container = trackScrollRef.current;
    if (!pan || !container) return;
    container.scrollLeft = pan.startScroll - (e.clientX - pan.startX);
  }

  function endPan(e: React.PointerEvent<HTMLDivElement>) {
    if (!panStateRef.current) return;
    panStateRef.current = null;
    setPanning(false);
    const container = trackScrollRef.current;
    if (container?.hasPointerCapture(e.pointerId)) {
      container.releasePointerCapture(e.pointerId);
    }
  }

  useEffect(() => {
    if (embedded) return; // state already seeded from initialSchema
    if (!crisisId) {
      setStatus({ kind: "idle" });
      return;
    }
    setStatus({ kind: "loading" });
    let cancelled = false;
    getForm(crisisId).then(
      ({ version, schema }) => {
        if (cancelled) return;
        dispatch({ type: "load", baseVersion: version, schema });
        setStatus({ kind: "ready" });
      },
      (err) => {
        if (cancelled) return;
        setStatus({ kind: "error", message: String((err as Error).message ?? err) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [crisisId, dispatch, embedded]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onResize = () => setIsNarrow(window.innerWidth < 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!state.dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [state.dirty]);

  async function confirmPublish() {
    if (embedded) {
      onSave(state.schema);
      setConfirmOpen(false);
      return;
    }
    if (status.kind !== "ready" || !crisisId) return;
    setPublishing(true);
    setValidationErrors([]);
    try {
      const result = await publishForm(crisisId, state.baseVersion, state.schema);
      if ("status" in result && result.status === "conflict") {
        setConflict({ version: result.current_version, schema: result.schema });
        setConfirmOpen(false);
        return;
      }
      if ("status" in result && result.status === "validation") {
        setValidationErrors(result.errors);
        setConfirmOpen(false);
        return;
      }
      dispatch({ type: "load", baseVersion: result.version, schema: result.schema });
      setConfirmOpen(false);
    } finally {
      setPublishing(false);
    }
  }

  function reloadFromConflict() {
    if (!conflict) return;
    dispatch({ type: "load", baseVersion: conflict.version, schema: conflict.schema });
    setConflict(null);
  }

  function openPreview() {
    // No crisisId guard: preview renders the in-memory schema and must work
    // for an unsaved crisis too. It never touches the API.
    if (status.kind !== "ready") return;
    setPreviewOpen(true);
  }

  if (!crisisId && !embedded) {
    return (
      <div style={{ padding: 24 }}>
        <p>Pick a crisis from the Crises page to edit its community form.</p>
      </div>
    );
  }
  if (isNarrow) {
    return (
      <div style={{ padding: 24, maxWidth: 480 }}>
        <h2>Use a desktop browser</h2>
        <p style={{ color: "var(--c-ink-3)" }}>
          The community-form builder is desktop-first. Open this page on a screen wider than 900px
          to edit.
        </p>
      </div>
    );
  }
  if (status.kind === "loading") {
    return <div style={{ padding: 24 }}>Loading form…</div>;
  }
  if (status.kind === "error") {
    return (
      <div style={{ padding: 24, color: "crimson" }}>Failed to load form: {status.message}</div>
    );
  }
  if (status.kind === "idle") return null;

  const pages = state.schema.pages;
  const pageIds = state.pageIds;
  const totalSteps = pages.filter((p) => p.enabled).length + 1; // +1 for Review

  // `targetIdx` is where the dragged page should now sit; reorders live.
  function handleDragOverPhone(targetIdx: number) {
    if (draggingPageIdx === null) return;
    if (targetIdx === draggingPageIdx) return;
    if (targetIdx < 2 || targetIdx >= pages.length) return;
    if (pages[targetIdx].locked) return;
    const key = { from: draggingPageIdx, to: targetIdx };
    const last = lastReorderRef.current;
    if (last && last.from === key.from && last.to === key.to) return;
    lastReorderRef.current = key;
    dispatch({ type: "reorderPages", from: draggingPageIdx, to: targetIdx });
    setDraggingPageIdx(targetIdx);
  }

  function endDrag() {
    setDraggingPageIdx(null);
    lastReorderRef.current = null;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <header className="page-hero is-compact">
        <div className="page-hero-inner">
          {onCancel && (
            <button type="button" className="btn secondary" onClick={onCancel}>
              ← Back to crisis
            </button>
          )}
          <div className="hero-title" style={{ flex: 1 }}>
            <h1>Community form</h1>
            <p>
              {embedded ? (
                <>
                  {state.dirty ? "Unsaved changes" : "No changes yet"} · {totalSteps} steps
                  including review
                </>
              ) : (
                <>
                  Version {state.baseVersion}
                  {state.dirty ? " · unsaved changes" : ""} · {totalSteps} steps including review
                </>
              )}
            </p>
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
            <button type="button" className="btn secondary" onClick={openPreview}>
              Preview
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setConfirmOpen(true)}
              disabled={!state.dirty || publishing}
            >
              {embedded ? "Save form" : "Publish update"}
            </button>
          </div>
        </div>
      </header>

      {validationErrors.length > 0 && (
        <div
          style={{
            margin: "16px 24px 0",
            background: "var(--c-danger-bg)",
            border: "1px solid var(--c-danger)",
            color: "var(--c-danger)",
            padding: 12,
            borderRadius: 8,
          }}
        >
          <strong>Schema validation failed:</strong>
          <ul style={{ margin: "6px 0 0 16px" }}>
            {validationErrors.map((e) => (
              <li key={`${e.path}:${e.message}`}>
                <code>{e.path}</code>: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!embedded && conflict && (
        <div
          style={{
            margin: "16px 24px 0",
            background: "var(--c-warn-bg)",
            border: "1px solid var(--c-warn)",
            color: "var(--c-warn)",
            padding: 12,
            borderRadius: 8,
          }}
        >
          Another coordinator published changes (version {conflict.version}). Reload to see them.
          Your local edits will be lost.
          <div style={{ marginTop: 8 }}>
            <button type="button" className="btn secondary" onClick={reloadFromConflict}>
              Reload
            </button>
          </div>
        </div>
      )}

      <div
        ref={trackScrollRef}
        style={{
          flex: 1,
          overflow: "auto",
          background: "var(--c-surface)",
          padding: "16px 24px",
          cursor: panning ? "grabbing" : "grab",
          userSelect: panning ? "none" : "auto",
        }}
        onDragOver={(e) => {
          // Accept drops anywhere so the cursor doesn't flash "not-allowed" between phones.
          if (draggingPageIdx !== null) e.preventDefault();
        }}
        onPointerDown={handleTrackPointerDown}
        onPointerMove={handleTrackPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
      >
        <div className="fb-track">
          {pages.map((page, idx) => (
            <Fragment key={pageIds[idx]}>
              <PagePhone
                page={page}
                pageIdx={idx}
                totalSteps={totalSteps}
                stepNumber={countEnabledThrough(pages, idx)}
                isDragging={draggingPageIdx === idx}
                draggingActive={draggingPageIdx !== null}
                onDragStart={() => {
                  setDraggingPageIdx(idx);
                  lastReorderRef.current = null;
                }}
                onDragEnd={endDrag}
                onDragOverPhone={() => handleDragOverPhone(idx)}
                dispatch={dispatch}
                selection={state.selection}
              />
              {idx >= 1 && idx < pages.length - 1 && draggingPageIdx === null && (
                <InsertGap onClick={() => dispatch({ type: "addGenericPage", afterIdx: idx })} />
              )}
            </Fragment>
          ))}
          <AddPageColumn
            onClick={() => dispatch({ type: "addGenericPage", afterIdx: pages.length - 1 })}
          />
          <ReviewPhone schema={state.schema} totalSteps={totalSteps} stepNumber={totalSteps} />
        </div>
      </div>

      {confirmOpen && (
        <ConfirmDialog
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void confirmPublish()}
          busy={publishing}
          embedded={embedded}
        />
      )}

      {previewOpen && (
        <PreviewModal
          schema={state.schema}
          totalSteps={totalSteps}
          dispatch={dispatch}
          selection={state.selection}
          onClose={() => setPreviewOpen(false)}
        />
      )}
    </div>
  );
}

function PreviewModal({
  schema,
  totalSteps,
  dispatch,
  selection,
  onClose,
}: {
  schema: import("../../types").FormSchema;
  totalSteps: number;
  dispatch: ReturnType<typeof useFormBuilderState>["dispatch"];
  selection: ReturnType<typeof useFormBuilderState>["state"]["selection"];
  onClose: () => void;
}) {
  const visiblePages = schema.pages
    .map((p, originalIdx) => ({ page: p, originalIdx }))
    .filter(({ page }) => page.enabled);
  const lastStep = visiblePages.length; // index of review step
  const [step, setStep] = useState(0);

  // Fill the available height; width follows a ~9:21 portrait aspect.
  const [viewport, setViewport] = useState(() =>
    typeof window === "undefined"
      ? { w: 1200, h: 900 }
      : { w: window.innerWidth, h: window.innerHeight },
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const phoneHeight = Math.min(viewport.h - 110, 900);
  // Capped at 40% of viewport width for ultrawide displays.
  const phoneWidth = Math.min(Math.round(phoneHeight * (9 / 21)), Math.round(viewport.w * 0.4));

  function next() {
    setStep((s) => Math.min(s + 1, lastStep));
  }
  function back() {
    setStep((s) => Math.max(s - 1, 0));
  }
  // Submit is a deliberate no-op in preview.
  function submit() {}

  const onPage = step < visiblePages.length;
  const current = onPage ? visiblePages[step] : null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        zIndex: 2000,
      }}
    >
      {/* The backdrop is a <button> for native keyboard semantics. It is a
          sibling of the content, not an ancestor, so no stopPropagation. */}
      <button
        type="button"
        aria-label="Close preview"
        onClick={onClose}
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(10,24,48,0.55)",
          border: "none",
          cursor: "pointer",
          padding: 0,
        }}
      />
      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 24px",
          color: "#fff",
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 600 }}>Preview: unsaved schema</div>
        <button
          type="button"
          className="btn secondary"
          onClick={onClose}
          style={{ padding: "6px 14px", fontSize: 13 }}
        >
          Close
        </button>
      </div>
      <div
        style={{
          position: "relative",
          flex: 1,
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "center",
          overflow: "auto",
          padding: "12px 24px 24px",
        }}
      >
        {current ? (
          <PagePhone
            key={current.originalIdx}
            page={current.page}
            pageIdx={current.originalIdx}
            totalSteps={totalSteps}
            stepNumber={step + 1}
            isDragging={false}
            draggingActive={false}
            onDragStart={() => {}}
            onDragEnd={() => {}}
            onDragOverPhone={() => {}}
            dispatch={dispatch}
            selection={selection}
            previewMode
            width={phoneWidth}
            height={phoneHeight}
            onPreviewBack={step > 0 ? back : undefined}
            onPreviewNext={next}
          />
        ) : (
          <ReviewPhone
            schema={schema}
            totalSteps={totalSteps}
            stepNumber={totalSteps}
            previewMode
            width={phoneWidth}
            height={phoneHeight}
            onPreviewBack={back}
            onPreviewSubmit={submit}
          />
        )}
      </div>
    </div>
  );
}

function countEnabledThrough(pages: import("../../types").FormPage[], idx: number): number {
  let n = 0;
  for (let i = 0; i <= idx; i += 1) {
    if (pages[i].enabled) n += 1;
  }
  return pages[idx]?.enabled ? n : 0;
}

function InsertGap({ onClick }: { onClick: () => void }) {
  // Between two phones, offset 36px to align with the screen; reveals on hover.
  return (
    <button
      type="button"
      onClick={onClick}
      className="fb-insert-gap"
      title="Insert page here"
      aria-label="Insert page here"
      style={{
        alignSelf: "flex-start",
        marginTop: 36 + 472 / 2 - 14,
        width: 28,
        height: 28,
        flexShrink: 0,
      }}
    >
      <svg width={14} height={14} viewBox="0 0 24 24" aria-hidden="true">
        <title>Insert page</title>
        <line
          x1="12"
          y1="4"
          x2="12"
          y2="20"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <line
          x1="4"
          y1="12"
          x2="20"
          y2="12"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
      </svg>
    </button>
  );
}

function AddPageColumn({ onClick }: { onClick: () => void }) {
  // Mirrors PagePhone's 30px toolbar + 6px gap so the tile sits flush with the screens.
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: 232,
        height: 472,
        marginLeft: 8,
        marginTop: 36,
        borderRadius: 22,
        border: "2px dashed var(--c-line)",
        background: "transparent",
        color: "var(--c-ink-3)",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        fontSize: 14,
        fontWeight: 600,
        flexShrink: 0,
        alignSelf: "flex-start",
      }}
    >
      <span style={{ fontSize: 32, lineHeight: 1 }}>+</span>
      Add page
    </button>
  );
}

function ConfirmDialog({
  onCancel,
  onConfirm,
  busy,
  embedded,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
  embedded: boolean;
}) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(10,24,48,0.45)",
        display: "grid",
        placeItems: "center",
        zIndex: 1500,
      }}
    >
      <div
        style={{
          background: "var(--c-card)",
          padding: 24,
          borderRadius: 14,
          maxWidth: 440,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          boxShadow: "var(--shadow-2)",
        }}
      >
        {embedded ? (
          <>
            <h3 style={{ margin: 0 }}>Save form?</h3>
            <p style={{ margin: 0, fontSize: 14, color: "var(--c-ink-3)" }}>
              This form will be used when the crisis is created. You can still edit it afterwards
              from the crisis detail page.
            </p>
          </>
        ) : (
          <>
            <h3 style={{ margin: 0 }}>Publish form update?</h3>
            <p style={{ margin: 0, fontSize: 14, color: "var(--c-ink-3)" }}>
              Citizens for this crisis will see the new form. Translations will run for any new or
              edited labels. Continue?
            </p>
          </>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
          <button type="button" className="btn secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn" onClick={onConfirm} disabled={busy}>
            {embedded ? "Save" : busy ? "Publishing…" : "Publish"}
          </button>
        </div>
      </div>
    </div>
  );
}
