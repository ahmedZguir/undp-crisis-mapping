// Inline editor for one generic-page question, addressed by (pageIdx,
// questionIdx). Only the label shows; type, Required, Max selectable and
// Delete sit behind the ⋮ menu to keep the phone preview uncluttered.
import { useEffect, useRef, useState } from "react";
import type { FormQuestion, FormQuestionType } from "../../../types";
import type { Dispatch } from "./useFormBuilderState";

interface Props {
  pageIdx: number;
  questionIdx: number;
  question: FormQuestion;
  selected: boolean;
  dispatch: Dispatch;
}

const TYPE_LABELS: Record<FormQuestionType, string> = {
  single_select: "Single select",
  multi_select: "Multi select",
  free_text: "Free text",
};

export function QuestionEditor({ pageIdx, questionIdx, question, selected, dispatch }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const cardRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onDown(e: MouseEvent) {
      if (!cardRef.current) return;
      if (!cardRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const select = () =>
    dispatch({
      type: "select",
      selection: { kind: "question", pageIdx, questionIdx },
    });

  return (
    <div
      ref={cardRef}
      onFocus={select}
      aria-label={`Question: ${question.label}`}
      className="fb-question-card"
      style={{
        border: selected ? "1.5px solid var(--c-blue-700)" : "1px solid var(--c-line)",
        borderRadius: 8,
        padding: 6,
        display: "flex",
        flexDirection: "column",
        gap: 5,
        background: "var(--c-card)",
        fontSize: 10,
        minWidth: 0,
        position: "relative",
      }}
    >
      <div style={{ display: "flex", gap: 4, alignItems: "center", minWidth: 0 }}>
        <button
          type="button"
          className="fb-mini-iconbtn"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
          title="Question settings"
          aria-label="Question settings"
          aria-expanded={menuOpen}
        >
          <DotsSvg />
        </button>
        <input
          type="text"
          className="fb-edit-input"
          value={question.label}
          onChange={(e) =>
            dispatch({
              type: "editQuestion",
              pageIdx,
              questionIdx,
              patch: { label: e.target.value },
            })
          }
          placeholder="Question label"
          title="Click to edit question label"
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 10,
            fontWeight: 600,
            padding: "3px 5px",
          }}
        />
        {question.required && (
          <span
            aria-label="Required"
            title="Required"
            style={{
              color: "var(--c-danger)",
              fontSize: 12,
              fontWeight: 700,
              lineHeight: 1,
              flexShrink: 0,
            }}
          >
            *
          </span>
        )}
      </div>

      {(question.type === "single_select" || question.type === "multi_select") && (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {(question.options ?? []).map((opt, optionIdx) => (
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
              key={optionIdx}
              style={{ display: "flex", gap: 4, alignItems: "center", minWidth: 0 }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: question.type === "multi_select" ? 2 : "50%",
                  border: "1.5px solid var(--c-ink-4)",
                  flexShrink: 0,
                }}
              />
              <input
                type="text"
                className="fb-edit-input"
                value={opt.label}
                onChange={(e) =>
                  dispatch({
                    type: "editOption",
                    pageIdx,
                    questionIdx,
                    optionIdx,
                    label: e.target.value,
                  })
                }
                placeholder="Option"
                title="Click to edit option"
                style={{ flex: 1, minWidth: 0, fontSize: 9, padding: "2px 4px" }}
              />
              <button
                type="button"
                className="fb-mini-iconbtn danger"
                onClick={(e) => {
                  e.stopPropagation();
                  dispatch({
                    type: "removeOption",
                    pageIdx,
                    questionIdx,
                    optionIdx,
                  });
                }}
                title="Remove option"
                aria-label="Remove option"
                style={{ width: 14, height: 14 }}
              >
                <XSvg />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="fb-add-option"
            onClick={(e) => {
              e.stopPropagation();
              dispatch({ type: "addOption", pageIdx, questionIdx });
            }}
          >
            + Option
          </button>
        </div>
      )}

      {question.type === "free_text" && (
        <textarea
          className="fb-edit-input"
          value={question.placeholder ?? ""}
          onChange={(e) =>
            dispatch({
              type: "editQuestion",
              pageIdx,
              questionIdx,
              patch: { placeholder: e.target.value },
            })
          }
          placeholder="Placeholder text…"
          title="Click to edit placeholder"
          rows={2}
          style={{
            fontSize: 9,
            padding: "5px 7px",
            fontStyle: "italic",
            border: "1.5px solid var(--c-line)",
            borderRadius: 6,
            background: "var(--c-card)",
            color: "var(--c-ink)",
            resize: "none",
            fontFamily: "inherit",
            width: "100%",
          }}
        />
      )}

      {menuOpen && (
        <SettingsPopover
          question={question}
          onChangeType={(t) =>
            dispatch({
              type: "editQuestion",
              pageIdx,
              questionIdx,
              patch: { type: t },
            })
          }
          onToggleRequired={(v) =>
            dispatch({
              type: "editQuestion",
              pageIdx,
              questionIdx,
              patch: { required: v },
            })
          }
          onChangeMax={(v) =>
            dispatch({
              type: "editQuestion",
              pageIdx,
              questionIdx,
              patch: { max_select: v },
            })
          }
          onDelete={() => {
            setMenuOpen(false);
            dispatch({ type: "removeQuestion", pageIdx, questionIdx });
          }}
          onClose={() => setMenuOpen(false)}
        />
      )}
    </div>
  );
}

interface PopoverProps {
  question: FormQuestion;
  onChangeType: (t: FormQuestionType) => void;
  onToggleRequired: (v: boolean) => void;
  onChangeMax: (v: number | undefined) => void;
  onDelete: () => void;
  onClose: () => void;
}

function SettingsPopover({
  question,
  onChangeType,
  onToggleRequired,
  onChangeMax,
  onDelete,
  onClose,
}: PopoverProps) {
  return (
    <div
      style={{
        position: "absolute",
        top: 26,
        left: 6,
        zIndex: 20,
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 8,
        boxShadow: "var(--shadow-2)",
        padding: 5,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        minWidth: 118,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 8,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: 0.4,
          color: "var(--c-ink-3)",
        }}
      >
        <span>Settings</span>
        <button
          type="button"
          className="fb-mini-iconbtn"
          onClick={onClose}
          aria-label="Close menu"
          style={{ width: 13, height: 13 }}
        >
          <XSvg />
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {(Object.keys(TYPE_LABELS) as FormQuestionType[]).map((t) => {
          const active = question.type === t;
          return (
            <button
              key={t}
              type="button"
              onClick={() => onChangeType(t)}
              style={{
                padding: "3px 6px",
                fontSize: 9,
                borderRadius: 5,
                border: `1px solid ${active ? "var(--c-blue-700)" : "var(--c-line)"}`,
                background: active ? "var(--c-blue-50)" : "var(--c-card)",
                color: active ? "var(--c-blue-700)" : "var(--c-ink-2)",
                fontWeight: active ? 700 : 500,
                cursor: "pointer",
                textAlign: "start",
              }}
            >
              {TYPE_LABELS[t]}
            </button>
          );
        })}
      </div>

      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          fontSize: 9,
          fontWeight: 600,
          color: "var(--c-ink-2)",
          cursor: "pointer",
        }}
      >
        <input
          type="checkbox"
          checked={question.required ?? false}
          onChange={(e) => onToggleRequired(e.target.checked)}
          style={{ accentColor: "var(--c-blue-700)", width: 10, height: 10 }}
        />
        Required
      </label>

      {question.type === "multi_select" && (
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: 9,
            fontWeight: 600,
            color: "var(--c-ink-2)",
          }}
        >
          Max
          <input
            type="number"
            min={1}
            max={(question.options ?? []).length || undefined}
            value={question.max_select ?? 1}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                onChangeMax(undefined);
                return;
              }
              const value = Number.parseInt(raw, 10);
              if (!Number.isFinite(value)) return;
              const cap = (question.options ?? []).length;
              const clamped = cap > 0 ? Math.min(Math.max(value, 1), cap) : Math.max(value, 1);
              onChangeMax(clamped);
            }}
            style={{
              width: 56,
              fontSize: 11,
              padding: "3px 6px",
              textAlign: "start",
              border: "1px solid var(--c-line)",
              borderRadius: 4,
              background: "var(--c-card)",
              color: "var(--c-ink)",
              outline: "none",
              fontFamily: "inherit",
            }}
          />
        </label>
      )}

      <button
        type="button"
        onClick={onDelete}
        style={{
          padding: "3px 6px",
          fontSize: 9,
          fontWeight: 700,
          borderRadius: 5,
          border: "1px solid var(--c-danger-bg)",
          background: "var(--c-danger-bg)",
          color: "var(--c-danger)",
          cursor: "pointer",
        }}
      >
        Delete
      </button>
    </div>
  );
}

function DotsSvg() {
  return (
    <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <title>Menu</title>
      <circle cx="12" cy="5" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="12" cy="19" r="1.8" />
    </svg>
  );
}

function XSvg() {
  return (
    <svg
      width={9}
      height={9}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <title>Remove</title>
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}
