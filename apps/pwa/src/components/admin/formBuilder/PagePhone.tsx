// One phone preview card per builder page: toolbar (drag handle, index,
// Enabled toggle, delete for generic pages), a mini render of the citizen
// step, and a caption.
//
// Built-in pages mimic the citizen step at small scale; selection there is
// local to the preview and never persisted. Generic pages render an editable
// title and question editors.
//
// The whole phone is a drop target, so the parent can reorder live without
// the admin aiming at a narrow gap.
import { useId, useState } from "react";
import chemIcon from "../../../assets/chem.png";
import civilIcon from "../../../assets/civil.png";
import commercialIcon from "../../../assets/commercial.png";
import communityIcon from "../../../assets/community.png";
import conflictIcon from "../../../assets/conflict.png";
import completeIcon from "../../../assets/damage-complete.webp";
import minimalIcon from "../../../assets/damage-minimal.webp";
import partialIcon from "../../../assets/damage-partial.webp";
import debrisNoIcon from "../../../assets/debris-no.webp";
import debrisYesIcon from "../../../assets/debris-yes.webp";
import eqIcon from "../../../assets/eq.png";
import explosIcon from "../../../assets/explos.png";
import floodIcon from "../../../assets/flood.png";
import governmentIcon from "../../../assets/governmental.png";
import hurricIcon from "../../../assets/hurric.png";
import landslideIcon from "../../../assets/landslide-icon.webp";
import publicIcon from "../../../assets/public.png";
import residentialIcon from "../../../assets/residential.png";
import transportIcon from "../../../assets/transport.png";
import tsunamiIcon from "../../../assets/tsunami.png";
import utilityIcon from "../../../assets/utility.png";
import wildfireIcon from "../../../assets/wildfire.png";
import type { FormPage, FormQuestion, FormQuestionType, FormSchema } from "../../../types";
import { AddQuestionMenu } from "./AddQuestionMenu";
import { QuestionEditor } from "./QuestionEditor";
import { filterCouplingWarning } from "./filterCouplingWarning";
import { type Dispatch, type Selection, canToggleRequired } from "./useFormBuilderState";

const PHONE_W = 232;
const PHONE_H = 472;
const FRAME_PAD = 6;

interface Props {
  page: FormPage;
  pageIdx: number;
  totalSteps: number;
  stepNumber: number;
  isDragging: boolean;
  draggingActive: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOverPhone: () => void;
  dispatch: Dispatch;
  selection: Selection;
  // Hides the builder chrome and renders generic pages as the citizen sees them.
  previewMode?: boolean;
  // Sizing override for the preview modal.
  width?: number;
  height?: number;
  // In-phone Back / Next, in preview mode.
  onPreviewBack?: () => void;
  onPreviewNext?: () => void;
}

function prettyKind(kind: string): string {
  return kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function PagePhone({
  page,
  pageIdx,
  totalSteps,
  stepNumber,
  isDragging,
  draggingActive,
  onDragStart,
  onDragEnd,
  onDragOverPhone,
  dispatch,
  selection,
  previewMode = false,
  width,
  height,
  onPreviewBack,
  onPreviewNext,
}: Props) {
  const warning = filterCouplingWarning(page.kind, page.enabled);
  const canDrag = pageIdx >= 2 && !page.locked;
  const canRemove = page.kind === "generic";

  return (
    <div
      className="fb-phone-wrap"
      onDragOver={(e) => {
        if (!draggingActive) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        if (!isDragging) onDragOverPhone();
      }}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
        flexShrink: 0,
        opacity: isDragging ? 0.35 : 1,
      }}
    >
      {!previewMode && (
        <PhoneToolbar
          page={page}
          pageIdx={pageIdx}
          canDrag={canDrag}
          canRemove={canRemove}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          dispatch={dispatch}
        />
      )}

      <AndroidPhone disabled={!page.enabled} width={width} height={height}>
        <PhoneStatusBar />
        <PhoneAppBar
          stepNumber={stepNumber}
          totalSteps={totalSteps}
          disabled={!page.enabled}
          onBack={onPreviewBack}
        />
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            overflowX: "hidden",
            padding: previewMode ? "14px 16px 18px" : "8px 10px 12px",
            display: "flex",
            flexDirection: "column",
            gap: previewMode ? 12 : 7,
            minWidth: 0,
          }}
        >
          {page.kind === "generic" ? (
            previewMode ? (
              <GenericPageRuntime page={page} onNext={onPreviewNext} />
            ) : (
              <GenericPageEditor
                page={page}
                pageIdx={pageIdx}
                dispatch={dispatch}
                selection={selection}
              />
            )
          ) : (
            <BuiltinPreview page={page} onNext={onPreviewNext} previewMode={previewMode} />
          )}
        </div>
      </AndroidPhone>

      {!previewMode && (
        <div
          style={{
            fontSize: 11,
            color: "var(--c-ink-3)",
            maxWidth: PHONE_W,
            textAlign: "center",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 4,
          }}
        >
          {page.locked && (
            <span className="fb-locked-badge">
              <LockSvg size={9} />
              Locked
            </span>
          )}
          {prettyKind(page.kind)}
          {page.kind === "generic" ? " · custom" : ""}
        </div>
      )}
      {!previewMode && warning && (
        <div
          style={{
            fontSize: 10,
            color: "var(--c-warn)",
            background: "var(--c-warn-bg)",
            borderRadius: 6,
            padding: "3px 6px",
            maxWidth: PHONE_W,
            textAlign: "center",
          }}
        >
          {warning}
        </div>
      )}
    </div>
  );
}

// Virtual end-of-list phone for the review step. Not in the schema, not draggable.
export function ReviewPhone({
  schema,
  totalSteps,
  stepNumber,
  previewMode = false,
  width,
  height,
  onPreviewBack,
  onPreviewSubmit,
}: {
  schema: FormSchema;
  totalSteps: number;
  stepNumber: number;
  previewMode?: boolean;
  width?: number;
  height?: number;
  onPreviewBack?: () => void;
  onPreviewSubmit?: () => void;
}) {
  return (
    <div
      className="fb-phone-wrap"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
        flexShrink: 0,
      }}
    >
      {!previewMode && (
        <div
          style={{
            width: PHONE_W,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 6,
            padding: "5px 8px",
            background: "var(--c-card)",
            border: "1px solid var(--c-line)",
            borderRadius: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="fb-locked-badge">
              <LockSvg size={9} />
              Locked
            </span>
            <strong style={{ fontSize: 11, color: "var(--c-ink-2)" }}>Review</strong>
          </div>
          <span style={{ fontSize: 10, color: "var(--c-ink-3)" }}>always last</span>
        </div>
      )}

      <AndroidPhone disabled={false} width={width} height={height}>
        <PhoneStatusBar />
        <PhoneAppBar
          stepNumber={stepNumber}
          totalSteps={totalSteps}
          disabled={false}
          onBack={onPreviewBack}
        />
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            overflowX: "hidden",
            padding: previewMode ? "14px 16px 18px" : "8px 10px 12px",
            display: "flex",
            flexDirection: "column",
            gap: previewMode ? 12 : 6,
            minWidth: 0,
          }}
        >
          <MiniTitle big={previewMode}>Review and submit</MiniTitle>
          <ReviewSummary schema={schema} big={previewMode} />
          <MiniPrimary onClick={onPreviewSubmit} big={previewMode}>
            Submit
          </MiniPrimary>
        </div>
      </AndroidPhone>

      <div
        style={{
          fontSize: 11,
          color: "var(--c-ink-3)",
          maxWidth: PHONE_W,
          textAlign: "center",
        }}
      >
        Review · built-in
      </div>
    </div>
  );
}

function ReviewSummary({ schema, big = false }: { schema: FormSchema; big?: boolean }) {
  const rows: Array<{ label: string; value: string }> = [];
  for (const page of schema.pages) {
    if (!page.enabled) continue;
    if (page.kind === "generic") {
      for (const q of page.questions ?? []) {
        rows.push({ label: q.label || "Question", value: sampleAnswer(q) });
      }
      continue;
    }
    rows.push({ label: prettyKind(page.kind), value: "—" });
  }
  if (rows.length === 0) {
    return (
      <div style={{ fontSize: big ? 13 : 10, color: "var(--c-ink-3)" }}>No fields enabled yet.</div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {rows.map((r, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: positional review rows
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: big ? "8px 0" : "4px 0",
            borderBottom: "1px solid var(--c-line-2)",
            fontSize: big ? 13 : 10,
          }}
        >
          <span
            style={{
              color: "var(--c-ink-3)",
              fontWeight: 600,
              flexShrink: 0,
              minWidth: big ? 90 : 60,
            }}
          >
            {r.label}
          </span>
          <span
            style={{
              flex: 1,
              minWidth: 0,
              color: "var(--c-ink)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={r.value}
          >
            {r.value}
          </span>
          <PencilSvg size={10} />
        </div>
      ))}
    </div>
  );
}

function sampleAnswer(q: FormQuestion): string {
  if (q.type === "free_text") return "—";
  const first = q.options?.[0]?.label;
  return first ?? "—";
}

function AndroidPhone({
  children,
  disabled,
  width = PHONE_W,
  height = PHONE_H,
}: {
  children: React.ReactNode;
  disabled: boolean;
  width?: number;
  height?: number;
}) {
  return (
    <div
      style={{
        width,
        height,
        background: "#1a1f2b",
        borderRadius: width >= 320 ? 36 : 22,
        padding: FRAME_PAD,
        boxShadow:
          "0 6px 18px rgba(10, 24, 48, 0.22), 0 0 0 1px rgba(0,0,0,0.4), inset 0 0 0 1px rgba(255,255,255,0.06)",
        opacity: disabled ? 0.55 : 1,
        position: "relative",
      }}
    >
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          top: 11,
          left: "50%",
          transform: "translateX(-50%)",
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: "#0a0a12",
          boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08)",
          zIndex: 3,
        }}
      />
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          right: -2,
          top: 78,
          width: 2,
          height: 38,
          background: "#0e131c",
          borderRadius: 2,
        }}
      />
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          left: -2,
          top: 68,
          width: 2,
          height: 24,
          background: "#0e131c",
          borderRadius: 2,
        }}
      />
      <div
        aria-hidden="true"
        style={{
          left: -2,
          top: 100,
          position: "absolute",
          width: 2,
          height: 24,
          background: "#0e131c",
          borderRadius: 2,
        }}
      />
      <div
        style={{
          flex: 1,
          width: "100%",
          height: "100%",
          background: "var(--c-card)",
          borderRadius: width >= 320 ? 28 : 16,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </div>
    </div>
  );
}

interface PhoneToolbarProps {
  page: FormPage;
  pageIdx: number;
  canDrag: boolean;
  canRemove: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  dispatch: Dispatch;
}

function PhoneToolbar({
  page,
  pageIdx,
  canDrag,
  canRemove,
  onDragStart,
  onDragEnd,
  dispatch,
}: PhoneToolbarProps) {
  return (
    <div
      draggable={canDrag}
      onDragStart={(e) => {
        if (!canDrag) {
          e.preventDefault();
          return;
        }
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", `page:${pageIdx}`);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      className={canDrag ? "fb-drag-handle" : undefined}
      style={{
        width: PHONE_W,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 6,
        padding: "5px 8px",
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 8,
        cursor: canDrag ? "grab" : "default",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <DragHandleSvg dim={!canDrag} />
        <strong style={{ fontSize: 11, color: "var(--c-ink-2)" }}>Page {pageIdx + 1}</strong>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {page.locked ? (
          <span className="fb-locked-badge">
            <LockSvg size={9} />
            Locked
          </span>
        ) : (
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 3,
              fontSize: 10,
              color: "var(--c-ink-2)",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={page.enabled}
              onChange={() => dispatch({ type: "togglePage", pageIdx })}
              style={{ width: 11, height: 11, accentColor: "var(--c-blue-700)" }}
            />
            Enabled
          </label>
        )}
        {page.enabled && canToggleRequired(page.kind) && (
          <label
            title="When off, residents may continue without answering this question."
            style={{
              display: "flex",
              alignItems: "center",
              gap: 3,
              fontSize: 10,
              color: "var(--c-ink-2)",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={page.required ?? true}
              onChange={() => dispatch({ type: "togglePageRequired", pageIdx })}
              style={{ width: 11, height: 11, accentColor: "var(--c-blue-700)" }}
            />
            Required
          </label>
        )}
        {canRemove && (
          <button
            type="button"
            onClick={() => dispatch({ type: "removePage", pageIdx })}
            title="Delete this page"
            aria-label="Delete page"
            className="fb-mini-iconbtn danger"
          >
            <TrashSvg size={11} />
          </button>
        )}
      </div>
    </div>
  );
}

function PhoneStatusBar() {
  // Static mock status bar.
  return (
    <div
      style={{
        height: 20,
        padding: "0 14px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "var(--c-card)",
        color: "var(--c-ink)",
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: 0.2,
      }}
    >
      <span>9:41</span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
        <SignalSvg />
        <WifiSvg />
        <BatterySvg />
      </span>
    </div>
  );
}

function PhoneAppBar({
  stepNumber,
  totalSteps,
  disabled,
  onBack,
}: {
  stepNumber: number;
  totalSteps: number;
  disabled: boolean;
  onBack?: () => void;
}) {
  const clickable = !!onBack;
  return (
    <div
      style={{
        background: "var(--c-card)",
        borderBottom: "1px solid var(--c-line-2)",
        padding: "8px 14px 6px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 6,
        }}
      >
        <button
          type="button"
          onClick={clickable ? onBack : undefined}
          disabled={!clickable}
          aria-label="Back"
          style={{
            width: 28,
            height: 28,
            border: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--c-ink-2)",
            background: "transparent",
            cursor: clickable ? "pointer" : "default",
            padding: 0,
          }}
        >
          <ChevronLeftSvg size={13} />
        </button>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--c-ink-3)" }}>
          {disabled ? "disabled" : `Step ${stepNumber} of ${totalSteps}`}
        </span>
        <div style={{ width: 28 }} />
      </div>
      <div style={{ display: "flex", gap: 2 }}>
        {Array.from({ length: totalSteps }).map((_, i) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: stepper dots are positional
            key={i}
            style={{
              flex: 1,
              height: 2,
              borderRadius: 999,
              background: i < stepNumber ? "var(--c-blue-700)" : "var(--c-line)",
            }}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic-page editor (inline question editing)
// ---------------------------------------------------------------------------

// Mirrors GenericPage.tsx with the mini styling. Selection is local and discarded on close.
function GenericPageRuntime({
  page,
  onNext,
}: {
  page: FormPage;
  onNext?: () => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const questions = page.questions ?? [];

  return (
    <>
      {page.title && (
        <h2
          style={{
            fontSize: 16,
            fontWeight: 700,
            color: "var(--c-ink)",
            margin: "0 0 6px",
            textAlign: "center",
          }}
        >
          {page.title}
        </h2>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {questions.map((q, qIdx) => (
          <RuntimeQuestion
            // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
            key={qIdx}
            question={q}
            value={answers[q.label]}
            onChange={(v) => setAnswers((prev) => ({ ...prev, [q.label]: v }))}
          />
        ))}
      </div>
      <MiniPrimary onClick={onNext} big>
        Next
      </MiniPrimary>
    </>
  );
}

function RuntimeQuestion({
  question,
  value,
  onChange,
}: {
  question: FormQuestion;
  value: string | string[] | undefined;
  onChange: (v: string | string[]) => void;
}) {
  const legend = (
    <span
      style={{
        fontSize: 13,
        fontWeight: 600,
        color: "var(--c-ink)",
        display: "block",
        marginBottom: 6,
      }}
    >
      {question.label}
      {question.required ? <span style={{ color: "var(--c-danger)" }}> *</span> : null}
      {question.type === "multi_select" && question.max_select !== undefined ? (
        <span style={{ fontWeight: 400, color: "var(--c-ink-3)", marginInlineStart: 6 }}>
          (max {question.max_select})
        </span>
      ) : null}
    </span>
  );

  if (question.type === "free_text") {
    return (
      <div>
        {legend}
        <textarea
          value={typeof value === "string" ? value : ""}
          placeholder={question.placeholder ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 10,
            border: "1.5px solid var(--c-line)",
            background: "var(--c-card)",
            fontSize: 13,
            color: "var(--c-ink)",
            outline: "none",
            resize: "vertical",
            fontFamily: "inherit",
          }}
        />
      </div>
    );
  }

  if (question.type === "single_select") {
    const selected = typeof value === "string" ? value : "";
    return (
      <div>
        {legend}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {(question.options ?? []).map((opt, idx) => {
            const isSel = selected === opt.label;
            return (
              <RuntimeChoice
                // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
                key={idx}
                kind="radio"
                label={opt.label}
                selected={isSel}
                onClick={() => onChange(opt.label)}
              />
            );
          })}
        </div>
      </div>
    );
  }

  const selectedLabels = Array.isArray(value) ? value : [];
  const max = question.max_select;
  function toggle(label: string) {
    if (selectedLabels.includes(label)) {
      onChange(selectedLabels.filter((l) => l !== label));
      return;
    }
    if (max !== undefined && selectedLabels.length >= max) return;
    onChange([...selectedLabels, label]);
  }
  return (
    <div>
      {legend}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {(question.options ?? []).map((opt, idx) => {
          const isSel = selectedLabels.includes(opt.label);
          return (
            <RuntimeChoice
              // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
              key={idx}
              kind="checkbox"
              label={opt.label}
              selected={isSel}
              onClick={() => toggle(opt.label)}
            />
          );
        })}
      </div>
    </div>
  );
}

function RuntimeChoice({
  kind,
  label,
  selected,
  onClick,
}: {
  kind: "radio" | "checkbox";
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "12px 14px",
        borderRadius: 12,
        border: `2px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
        background: selected ? "var(--c-blue-50)" : "var(--c-card)",
        fontSize: 14,
        fontWeight: 600,
        color: "var(--c-ink)",
        textAlign: "start",
        cursor: "pointer",
        width: "100%",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 18,
          height: 18,
          flexShrink: 0,
          borderRadius: kind === "radio" ? "50%" : 4,
          border: `2px solid ${selected ? "var(--c-blue-700)" : "var(--c-ink-4)"}`,
          background: selected ? "var(--c-blue-700)" : "transparent",
          display: "grid",
          placeItems: "center",
          color: "#fff",
          fontSize: 11,
          fontWeight: 700,
        }}
      >
        {selected ? (kind === "radio" ? "●" : "✓") : ""}
      </span>
      <span>{label}</span>
    </button>
  );
}

function GenericPageEditor({
  page,
  pageIdx,
  dispatch,
  selection,
}: {
  page: FormPage;
  pageIdx: number;
  dispatch: Dispatch;
  selection: Selection;
}) {
  const [addingQuestion, setAddingQuestion] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  const questions = page.questions ?? [];

  function handleDrop(targetGap: number) {
    if (dragFrom === null) return;
    let to = targetGap;
    if (targetGap > dragFrom) to = targetGap - 1;
    if (to < 0) to = 0;
    if (to >= questions.length) to = questions.length - 1;
    dispatch({ type: "moveQuestion", pageIdx, from: dragFrom, to });
    setDragFrom(null);
    setDragOver(null);
  }

  return (
    <>
      <input
        type="text"
        className="fb-edit-input fb-edit-title"
        value={page.title ?? ""}
        placeholder="Page title"
        onChange={(e) => dispatch({ type: "editPageTitle", pageIdx, title: e.target.value })}
        title="Click to edit page title"
        style={{
          fontSize: 13,
          padding: "4px 6px",
          textAlign: "center",
          width: "100%",
        }}
      />
      {questions.map((q, qIdx) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
        <div key={qIdx} style={{ minWidth: 0 }}>
          <QuestionDropZone
            visible={dragFrom !== null && dragFrom !== qIdx}
            active={dragOver === qIdx}
            onEnter={() => setDragOver(qIdx)}
            onLeave={() => setDragOver(null)}
            onDrop={() => handleDrop(qIdx)}
          />
          <div
            draggable
            onDragStart={(e) => {
              e.stopPropagation();
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData("text/plain", `q:${pageIdx}:${qIdx}`);
              setDragFrom(qIdx);
            }}
            onDragEnd={() => {
              setDragFrom(null);
              setDragOver(null);
            }}
            style={{ opacity: dragFrom === qIdx ? 0.4 : 1, minWidth: 0 }}
          >
            <QuestionEditor
              pageIdx={pageIdx}
              questionIdx={qIdx}
              question={q}
              selected={
                selection?.kind === "question" &&
                selection.pageIdx === pageIdx &&
                selection.questionIdx === qIdx
              }
              dispatch={dispatch}
            />
          </div>
        </div>
      ))}
      <QuestionDropZone
        visible={dragFrom !== null && dragFrom !== questions.length - 1}
        active={dragOver === questions.length}
        onEnter={() => setDragOver(questions.length)}
        onLeave={() => setDragOver(null)}
        onDrop={() => handleDrop(questions.length)}
      />

      {addingQuestion ? (
        <AddQuestionMenu
          onPick={(qType: FormQuestionType) => {
            dispatch({ type: "addQuestion", pageIdx, qType });
            setAddingQuestion(false);
          }}
          onCancel={() => setAddingQuestion(false)}
        />
      ) : (
        <button
          type="button"
          onClick={() => setAddingQuestion(true)}
          style={{
            alignSelf: "stretch",
            padding: "5px 8px",
            fontSize: 10,
            background: "var(--c-blue-50)",
            color: "var(--c-blue-700)",
            border: "1px dashed var(--c-blue-300)",
            borderRadius: 8,
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          + Add question
        </button>
      )}
    </>
  );
}

function QuestionDropZone({
  visible,
  active,
  onEnter,
  onLeave,
  onDrop,
}: {
  visible: boolean;
  active: boolean;
  onEnter: () => void;
  onLeave: () => void;
  onDrop: () => void;
}) {
  if (!visible) return <div style={{ height: 1 }} />;
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        onEnter();
      }}
      onDragLeave={onLeave}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onDrop();
      }}
      style={{
        height: active ? 12 : 5,
        margin: "1px 0",
        borderRadius: 4,
        background: active ? "var(--c-blue-700)" : "var(--c-blue-200)",
        transition: "height 100ms ease",
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Built-in previews
// ---------------------------------------------------------------------------

function BuiltinPreview({
  page,
  onNext,
  previewMode,
}: {
  page: FormPage;
  onNext?: () => void;
  previewMode: boolean;
}) {
  const big = previewMode;
  switch (page.kind) {
    case "photo_and_damage":
      return <PhotoPreview onNext={onNext} big={big} />;
    case "description":
      return <DescriptionPreview onNext={onNext} big={big} />;
    case "location":
      return <LocationPreview onNext={onNext} big={big} />;
    case "debris":
      return <DebrisPreview onNext={onNext} big={big} />;
    case "infra_type":
      return <InfraTypePreview onNext={onNext} big={big} />;
    case "crisis_nature":
      return <CrisisNaturePreview onNext={onNext} big={big} />;
    case "electricity":
      return <ElectricityPreview onNext={onNext} big={big} />;
    case "health_services":
      return <HealthPreview onNext={onNext} big={big} />;
    case "pressing_needs":
      return <PressingNeedsPreview onNext={onNext} big={big} />;
    default:
      return null;
  }
}

interface PreviewSubProps {
  onNext?: () => void;
  big?: boolean;
}

function useLocalSelect<T>(initial: T | null = null) {
  return useState<T | null>(initial);
}
function useLocalMultiSelect<T>(initial: T[] = []) {
  return useState<T[]>(initial);
}

function PhotoPreview({ onNext, big }: PreviewSubProps) {
  const [pick, setPick] = useLocalSelect<string>();
  return (
    <>
      <MiniTitle big={big}>Add a photo</MiniTitle>
      <div
        style={{
          background: "#1a1a2a",
          borderRadius: big ? 14 : 10,
          height: big ? 140 : 84,
          display: "grid",
          placeItems: "center",
          color: "rgba(255,255,255,0.7)",
          fontSize: big ? 13 : 9,
          flexShrink: 0,
        }}
      >
        <div style={{ textAlign: "center" }}>
          <CameraSvg size={big ? 32 : 20} />
          <div style={{ marginTop: big ? 6 : 2 }}>Tap to capture</div>
        </div>
      </div>
      <div
        style={{
          fontSize: big ? 14 : 11,
          fontWeight: 700,
          color: "var(--c-ink)",
          marginTop: big ? 6 : 2,
        }}
      >
        How severe is the damage?
      </div>
      <TileColumn big={big}>
        {[
          {
            v: "minimal",
            l: "Minimal / No damage",
            d: "Cosmetic or none",
            icon: minimalIcon,
            bg: "var(--c-safe-bg)",
            border: "var(--c-safe)",
          },
          {
            v: "partial",
            l: "Partially damaged",
            d: "Repairable",
            icon: partialIcon,
            bg: "var(--c-warn-bg)",
            border: "var(--c-warn)",
          },
          {
            v: "complete",
            l: "Completely damaged",
            d: "Unsafe / destroyed",
            icon: completeIcon,
            bg: "var(--c-danger-bg)",
            border: "var(--c-danger)",
          },
        ].map((o) => (
          <DamageTile
            key={o.v}
            label={o.l}
            desc={o.d}
            icon={o.icon}
            selected={pick === o.v}
            selectedBg={o.bg}
            selectedBorder={o.border}
            onClick={() => setPick(pick === o.v ? null : o.v)}
            big={big}
          />
        ))}
      </TileColumn>
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

function DescriptionPreview({ onNext, big }: PreviewSubProps) {
  const [val, setVal] = useState("");
  return (
    <>
      <MiniTitle big={big}>Describe the building</MiniTitle>
      <textarea
        value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder="Example: 3-storey school building, front wall collapsed."
        style={{
          width: "100%",
          height: big ? 160 : 100,
          borderRadius: big ? 12 : 10,
          border: `${big ? 2 : 1.5}px solid var(--c-line)`,
          background: "var(--c-card)",
          fontSize: big ? 13 : 9,
          color: "var(--c-ink)",
          padding: big ? 10 : 6,
          resize: "none",
          outline: "none",
          fontFamily: "inherit",
        }}
      />
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

function LocationPreview({ onNext, big }: PreviewSubProps) {
  const [name, setName] = useState("");
  const [route, setRoute] = useState("");
  const nameId = useId();
  const routeId = useId();
  const inputStyle = big ? miniInputStyleBig : miniInputStyle;
  return (
    <>
      <MiniTitle big={big}>Confirm location</MiniTitle>

      <label
        htmlFor={nameId}
        style={{
          display: "block",
          fontSize: big ? 11 : 8,
          fontWeight: 700,
          color: "var(--c-ink-2)",
          letterSpacing: 0.04,
          textTransform: "uppercase",
          marginBottom: big ? 4 : 2,
        }}
      >
        Infrastructure name{" "}
        <span aria-hidden="true" style={{ color: "var(--c-danger)" }}>
          *
        </span>
      </label>
      <div
        style={{
          fontSize: big ? 11 : 8,
          color: "var(--c-ink-3)",
          lineHeight: 1.4,
          marginBottom: big ? 6 : 3,
        }}
      >
        Example: Al-Nour Primary School, Block B
      </div>
      <input
        id={nameId}
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        style={inputStyle}
      />

      <div
        style={{
          position: "relative",
          height: big ? 200 : 110,
          borderRadius: big ? 12 : 10,
          overflow: "hidden",
          border: "1px solid var(--c-line)",
          marginTop: big ? 10 : 6,
          background:
            "linear-gradient(135deg, var(--c-blue-100) 0%, var(--c-blue-200) 55%, var(--c-blue-300) 100%)",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -65%)",
          }}
        >
          <PinSvg size={big ? 24 : 14} />
        </div>
        <div
          style={{
            position: "absolute",
            top: big ? 8 : 4,
            right: big ? 8 : 4,
            width: big ? 32 : 22,
            height: big ? 32 : 22,
            borderRadius: 7,
            background: "var(--c-card)",
            border: "1px solid var(--c-line)",
            color: "var(--c-blue-700)",
            display: "grid",
            placeItems: "center",
            boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
          }}
        >
          <CrosshairSvg size={big ? 16 : 11} />
        </div>
      </div>

      <details style={{ marginTop: big ? 10 : 6 }}>
        <summary
          style={{
            fontSize: big ? 13 : 9,
            color: "var(--c-blue-700)",
            cursor: "pointer",
            fontWeight: 500,
            listStyle: "none",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <PinSvg size={big ? 13 : 9} />
          No GPS?
        </summary>
        <div style={{ marginTop: big ? 6 : 3 }}>
          <label
            htmlFor={routeId}
            style={{
              display: "block",
              fontSize: big ? 11 : 8,
              fontWeight: 600,
              color: "var(--c-ink-2)",
              marginBottom: big ? 3 : 2,
            }}
          >
            Location description (how to find this place)
          </label>
          <textarea
            id={routeId}
            value={route}
            onChange={(e) => setRoute(e.target.value)}
            placeholder="Next to the central market opposite the green mosque…"
            rows={big ? 3 : 2}
            style={{ ...inputStyle, resize: "none" }}
          />
        </div>
      </details>

      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

function DebrisPreview({ onNext, big }: PreviewSubProps) {
  const [pick, setPick] = useLocalSelect<string>();
  return (
    <>
      <MiniTitle big={big}>Is there debris blocking access?</MiniTitle>
      <TileColumn big={big}>
        {[
          {
            v: "yes",
            l: "Yes",
            icon: debrisYesIcon,
            bg: "var(--c-warn-bg)",
            border: "var(--c-warn)",
          },
          { v: "no", l: "No", icon: debrisNoIcon, bg: "var(--c-safe-bg)", border: "var(--c-safe)" },
          {
            v: "unknown",
            l: "Unknown / I don't know",
            bg: "var(--c-line-2)",
            border: "var(--c-ink-3)",
          },
        ].map((o) => (
          <DamageTile
            key={o.v}
            label={o.l}
            icon={o.icon}
            selected={pick === o.v}
            selectedBg={o.bg}
            selectedBorder={o.border}
            onClick={() => setPick(pick === o.v ? null : o.v)}
            big={big}
          />
        ))}
      </TileColumn>
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

function InfraTypePreview({ onNext, big }: PreviewSubProps) {
  const [picks, setPicks] = useLocalMultiSelect<string>();
  const opts = [
    { v: "residential", l: "Residential Infrastructure", icon: residentialIcon },
    { v: "commercial", l: "Commercial Infrastructure", icon: commercialIcon },
    { v: "government", l: "Government Building", icon: governmentIcon },
    { v: "utility", l: "Utility Infrastructure", icon: utilityIcon },
    { v: "transport", l: "Transport / Communication", icon: transportIcon },
    { v: "community", l: "Community Infrastructure", icon: communityIcon },
    { v: "public_spaces", l: "Public Spaces / Recreation", icon: publicIcon },
  ];
  function toggle(v: string) {
    setPicks((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]));
  }
  return (
    <>
      <MiniTitle big={big}>What type of infrastructure?</MiniTitle>
      <TileColumn big={big}>
        {opts.map((o) => (
          <IconRowTile
            key={o.v}
            label={o.l}
            icon={o.icon}
            selected={picks.includes(o.v)}
            onClick={() => toggle(o.v)}
            big={big}
          />
        ))}
      </TileColumn>
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

interface NatureGroup {
  label: string;
  color: string;
  subs: Array<{ v: string; l: string; icon: string; sub?: string }>;
}

const NATURE_GROUPS: NatureGroup[] = [
  {
    label: "Natural Hazards",
    color: "#0369a1",
    subs: [
      { v: "earthquake", l: "Earthquake", icon: eqIcon },
      { v: "flood", l: "Flood", icon: floodIcon },
      { v: "tsunami", l: "Tsunami", icon: tsunamiIcon },
      { v: "hurricane", l: "Hurricane", icon: hurricIcon, sub: "Cyclone / Typhoon" },
      { v: "landslide", l: "Landslide", icon: landslideIcon },
      { v: "wildfire", l: "Wildfire", icon: wildfireIcon },
    ],
  },
  {
    label: "Technological / Industrial",
    color: "#6d28d9",
    subs: [
      { v: "explosion", l: "Explosion", icon: explosIcon },
      { v: "chemical", l: "Chemical incident", icon: chemIcon },
    ],
  },
  {
    label: "Human-made",
    color: "#b91c1c",
    subs: [
      { v: "conflict", l: "Conflict", icon: conflictIcon },
      { v: "civil", l: "Civil unrest", icon: civilIcon },
    ],
  },
];

function CrisisNaturePreview({ onNext, big }: PreviewSubProps) {
  const [pick, setPick] = useLocalSelect<string>();
  return (
    <>
      <MiniTitle big={big}>Nature of the crisis</MiniTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: big ? 14 : 8 }}>
        {NATURE_GROUPS.map((g) => (
          <div key={g.label} style={{ display: "flex", flexDirection: "column", gap: big ? 6 : 4 }}>
            <div
              style={{
                fontSize: big ? 12 : 9,
                fontWeight: 700,
                color: g.color,
                textTransform: "uppercase",
                letterSpacing: 0.4,
              }}
            >
              {g.label}
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: big ? 8 : 4,
                minWidth: 0,
              }}
            >
              {g.subs.map((s) => {
                const selected = pick === s.v;
                return (
                  <button
                    key={s.v}
                    type="button"
                    onClick={() => setPick(selected ? null : s.v)}
                    aria-pressed={selected}
                    className={`fb-option-tile${selected ? " selected" : ""}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: big ? 8 : 5,
                      padding: big ? "10px 10px" : "5px 6px",
                      borderRadius: big ? 10 : 8,
                      border: `${big ? 2 : 1.5}px solid ${selected ? "var(--c-blue-600)" : "var(--c-line)"}`,
                      background: selected ? "var(--c-blue-100)" : "var(--c-card)",
                      cursor: "pointer",
                      minWidth: 0,
                      textAlign: "start",
                    }}
                  >
                    <img
                      src={s.icon}
                      alt=""
                      aria-hidden="true"
                      width={big ? 22 : 18}
                      height={big ? 22 : 18}
                      style={{ objectFit: "contain", flexShrink: 0 }}
                    />
                    <span
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        minWidth: 0,
                        flex: 1,
                      }}
                    >
                      <span
                        style={{
                          fontSize: big ? 11 : 9,
                          fontWeight: 600,
                          color: selected ? "var(--c-blue-800)" : "var(--c-ink)",
                          lineHeight: 1.2,
                          wordBreak: "break-word",
                        }}
                      >
                        {s.l}
                      </span>
                      {s.sub && (
                        <span
                          style={{
                            fontSize: big ? 9 : 7,
                            color: selected ? "var(--c-blue-800)" : "var(--c-ink-3)",
                            lineHeight: 1.2,
                            marginTop: big ? 2 : 1,
                          }}
                        >
                          {s.sub}
                        </span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <MiniPrimary onClick={onNext} big={big}>
        Review and submit
      </MiniPrimary>
    </>
  );
}

function ElectricityPreview({ onNext, big }: PreviewSubProps) {
  const [pick, setPick] = useLocalSelect<string>();
  const opts = [
    "Fully functional",
    "Partially functional",
    "Not functional",
    "Unknown",
    "No electricity infrastructure",
    "Damaged but repairable",
  ];
  return (
    <>
      <MiniTitle big={big}>Status of electricity</MiniTitle>
      <TileColumn big={big}>
        {opts.map((o) => (
          <PlainSelectableRow
            key={o}
            label={o}
            selected={pick === o}
            onClick={() => setPick(pick === o ? null : o)}
            big={big}
          />
        ))}
      </TileColumn>
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

function HealthPreview({ onNext, big }: PreviewSubProps) {
  const [pick, setPick] = useLocalSelect<string>();
  const opts = [
    "Fully functioning",
    "Partially functioning",
    "Not functioning",
    "Unknown",
    "No health facility nearby",
  ];
  return (
    <>
      <MiniTitle big={big}>Status of health services</MiniTitle>
      <TileColumn big={big}>
        {opts.map((o) => (
          <PlainSelectableRow
            key={o}
            label={o}
            selected={pick === o}
            onClick={() => setPick(pick === o ? null : o)}
            big={big}
          />
        ))}
      </TileColumn>
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

function PressingNeedsPreview({ onNext, big }: PreviewSubProps) {
  const [picks, setPicks] = useLocalMultiSelect<string>();
  const opts = [
    "Food",
    "Water",
    "Shelter",
    "Medical care",
    "Search and rescue",
    "Psychosocial support",
    "Cash assistance",
    "Other",
  ];
  function toggle(v: string) {
    setPicks((cur) =>
      cur.includes(v) ? cur.filter((x) => x !== v) : cur.length < 3 ? [...cur, v] : cur,
    );
  }
  return (
    <>
      <MiniTitle big={big}>What are the pressing needs?</MiniTitle>
      <div
        style={{
          fontSize: big ? 12 : 9,
          color: "var(--c-ink-3)",
          marginTop: -4,
          textAlign: "center",
        }}
      >
        Select up to 3
      </div>
      <div
        style={{ display: "flex", flexWrap: "wrap", gap: big ? 8 : 4, justifyContent: "center" }}
      >
        {opts.map((o) => {
          const selected = picks.includes(o);
          return (
            <button
              key={o}
              type="button"
              onClick={() => toggle(o)}
              className={`fb-option-tile${selected ? " selected" : ""}`}
              style={{
                padding: big ? "8px 14px" : "4px 7px",
                fontSize: big ? 13 : 9,
                borderRadius: 999,
                border: `${big ? 2 : 1.5}px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
                background: selected ? "var(--c-blue-50)" : "var(--c-card)",
                color: selected ? "var(--c-blue-700)" : "var(--c-ink-2)",
                fontWeight: selected ? 700 : 500,
                cursor: "pointer",
              }}
            >
              {o}
            </button>
          );
        })}
      </div>
      <MiniPrimary onClick={onNext} big={big}>
        Next
      </MiniPrimary>
    </>
  );
}

// ---------------------------------------------------------------------------
// Tile primitives
// ---------------------------------------------------------------------------

function MiniTitle({ children, big }: { children: React.ReactNode; big?: boolean }) {
  return (
    <h2
      style={{
        fontSize: big ? 17 : 12,
        fontWeight: 700,
        color: "var(--c-ink)",
        margin: big ? "0 0 8px" : "0 0 4px",
        lineHeight: 1.25,
        textAlign: "center",
      }}
    >
      {children}
    </h2>
  );
}

function MiniPrimary({
  children,
  onClick,
  big,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  big?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick ?? ((e) => e.preventDefault())}
      className="fb-primary"
      style={{
        marginTop: big ? 16 : "auto",
        height: big ? 46 : 28,
        padding: big ? "0 18px" : "0 10px",
        borderRadius: big ? 12 : 8,
        background: "var(--c-blue-700)",
        color: "#fff",
        fontSize: big ? 13 : 10,
        fontWeight: 600,
        letterSpacing: 0.1,
        lineHeight: 1,
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  );
}

function TileColumn({ children, big }: { children: React.ReactNode; big?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: big ? 8 : 4 }}>{children}</div>
  );
}

function DamageTile({
  label,
  desc,
  icon,
  selected,
  selectedBg,
  selectedBorder,
  onClick,
  big,
}: {
  label: string;
  desc?: string;
  icon?: string;
  selected: boolean;
  selectedBg: string;
  selectedBorder: string;
  onClick: () => void;
  big?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`fb-option-tile${selected ? " selected" : ""}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: big ? 10 : 7,
        padding: big ? "10px 12px" : "5px 7px",
        borderRadius: big ? 12 : 9,
        border: `${big ? 2 : 1.5}px solid ${selected ? selectedBorder : "var(--c-line)"}`,
        background: selected ? selectedBg : "var(--c-card)",
        cursor: "pointer",
        textAlign: "start",
        minWidth: 0,
      }}
    >
      {icon ? (
        <img
          src={icon}
          alt=""
          aria-hidden="true"
          width={big ? 32 : 20}
          height={big ? 32 : 20}
          style={{ objectFit: "contain", flexShrink: 0 }}
        />
      ) : (
        <span
          aria-hidden="true"
          style={{
            width: big ? 32 : 20,
            height: big ? 32 : 20,
            borderRadius: "50%",
            background: "var(--c-line-2)",
            flexShrink: 0,
          }}
        />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: big ? 13 : 10,
            fontWeight: 700,
            color: "var(--c-ink)",
            lineHeight: 1.2,
          }}
        >
          {label}
        </div>
        {desc && (
          <div
            style={{
              fontSize: big ? 11 : 8,
              color: "var(--c-ink-3)",
              marginTop: 2,
              lineHeight: 1.25,
            }}
          >
            {desc}
          </div>
        )}
      </div>
    </button>
  );
}

function IconRowTile({
  label,
  icon,
  selected,
  onClick,
  big,
}: {
  label: string;
  icon: string;
  selected: boolean;
  onClick: () => void;
  big?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`fb-option-tile${selected ? " selected" : ""}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: big ? 10 : 5,
        padding: big ? "10px 12px" : "4px 6px",
        borderRadius: big ? 10 : 7,
        border: `${big ? 2 : 1.5}px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
        background: selected ? "var(--c-blue-50)" : "var(--c-card)",
        cursor: "pointer",
        textAlign: "start",
        minWidth: 0,
      }}
    >
      <img
        src={icon}
        alt=""
        aria-hidden="true"
        width={big ? 28 : 16}
        height={big ? 28 : 16}
        style={{ objectFit: "contain", flexShrink: 0 }}
      />
      <span
        style={{
          fontSize: big ? 13 : 9,
          fontWeight: 600,
          color: "var(--c-ink)",
          flex: 1,
          minWidth: 0,
          lineHeight: 1.25,
        }}
      >
        {label}
      </span>
    </button>
  );
}

function PlainSelectableRow({
  label,
  selected,
  onClick,
  big,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
  big?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`fb-option-tile${selected ? " selected" : ""}`}
      style={{
        padding: big ? "12px 14px" : "5px 8px",
        borderRadius: big ? 10 : 7,
        border: `${big ? 2 : 1.5}px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
        background: selected ? "var(--c-blue-50)" : "var(--c-card)",
        fontSize: big ? 13 : 9,
        fontWeight: selected ? 700 : 500,
        color: selected ? "var(--c-blue-700)" : "var(--c-ink-2)",
        cursor: "pointer",
        textAlign: "start",
        minWidth: 0,
      }}
    >
      {label}
    </button>
  );
}

const miniInputStyleBig: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  fontSize: 13,
  fontFamily: "inherit",
  background: "var(--c-card)",
  border: "2px solid var(--c-line)",
  borderRadius: 10,
  color: "var(--c-ink)",
  outline: "none",
};

const miniInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "5px 7px",
  fontSize: 9,
  fontFamily: "inherit",
  background: "var(--c-card)",
  border: "1.5px solid var(--c-line)",
  borderRadius: 6,
  color: "var(--c-ink)",
  outline: "none",
};

// ---------------------------------------------------------------------------
// SVG icons
// ---------------------------------------------------------------------------

function LockSvg({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Locked</title>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

function ChevronLeftSvg({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Back</title>
      <polyline points="15 6 9 12 15 18" />
    </svg>
  );
}

function TrashSvg({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Delete</title>
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function DragHandleSvg({ dim }: { dim: boolean }) {
  const c = dim ? "var(--c-ink-4)" : "var(--c-ink-2)";
  return (
    <svg width={10} height={14} viewBox="0 0 10 14" fill={c} aria-hidden="true">
      <title>Drag to reorder</title>
      <circle cx="2" cy="2" r="1.2" />
      <circle cx="2" cy="7" r="1.2" />
      <circle cx="2" cy="12" r="1.2" />
      <circle cx="8" cy="2" r="1.2" />
      <circle cx="8" cy="7" r="1.2" />
      <circle cx="8" cy="12" r="1.2" />
    </svg>
  );
}

function CameraSvg({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Camera</title>
      <path d="M3 7h3l2-3h8l2 3h3v12H3z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  );
}

function CrosshairSvg({ size = 14 }: { size?: number }) {
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
      <title>Use my location</title>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
    </svg>
  );
}

function PinSvg({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="var(--c-danger)"
      stroke="#fff"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Map pin</title>
      <path d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7z" />
      <circle cx="12" cy="9" r="2.4" fill="#fff" />
    </svg>
  );
}

function PencilSvg({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--c-blue-700)"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Edit</title>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4z" />
    </svg>
  );
}

function SignalSvg() {
  return (
    <svg width={9} height={9} viewBox="0 0 9 9" fill="currentColor" aria-hidden="true">
      <title>Signal</title>
      <rect x="0" y="6" width="1.5" height="3" />
      <rect x="2.5" y="4" width="1.5" height="5" />
      <rect x="5" y="2" width="1.5" height="7" />
      <rect x="7.5" y="0" width="1.5" height="9" />
    </svg>
  );
}

function WifiSvg() {
  return (
    <svg
      width={10}
      height={9}
      viewBox="0 0 10 9"
      fill="none"
      stroke="currentColor"
      strokeWidth="1"
      aria-hidden="true"
    >
      <title>Wi-Fi</title>
      <path d="M1 3.4A6 6 0 0 1 9 3.4" />
      <path d="M2.5 5.2A4 4 0 0 1 7.5 5.2" />
      <circle cx="5" cy="7" r="0.7" fill="currentColor" />
    </svg>
  );
}

function BatterySvg() {
  return (
    <svg width={14} height={8} viewBox="0 0 14 8" fill="none" aria-hidden="true">
      <title>Battery</title>
      <rect x="0.5" y="0.5" width="11" height="7" rx="1.2" stroke="currentColor" />
      <rect x="2" y="2" width="6" height="4" fill="currentColor" />
      <rect x="12" y="2.5" width="1.5" height="3" rx="0.4" fill="currentColor" />
    </svg>
  );
}
