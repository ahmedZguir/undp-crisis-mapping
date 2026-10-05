// Admin-authored generic page; answers live in `formState.generic_answers`, keyed by question label.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { FormPage, FormQuestion } from "../../types";
import { StepShell } from "../StepShell";

interface GenericPageProps {
  page: FormPage;
  answers: Record<string, string | string[]>;
  // string for single-select / free-text; option labels for multi-select.
  onChange: (questionLabel: string, value: string | string[]) => void;
  onNext: () => void;
  onBack: () => void;
  stepNumber: number;
  totalSteps: number;
}

function isQuestionAnswered(q: FormQuestion, value: string | string[] | undefined): boolean {
  if (!q.required) return true;
  if (value === undefined) return false;
  if (Array.isArray(value)) return value.length > 0;
  return value.trim() !== "";
}

export function GenericPage({
  page,
  answers,
  onChange,
  onNext,
  onBack,
  stepNumber,
  totalSteps,
}: GenericPageProps) {
  const { t } = useTranslation();
  const [showValidation, setShowValidation] = useState(false);
  const questions = page.questions ?? [];
  const allAnswered = questions.every((q) => isQuestionAnswered(q, answers[q.label]));

  function handleNext() {
    if (!allAnswered) {
      setShowValidation(true);
      return;
    }
    onNext();
  }

  return (
    <StepShell
      stepNumber={stepNumber}
      totalSteps={totalSteps}
      onBack={onBack}
      footer={
        <button
          type="button"
          onClick={handleNext}
          disabled={!allAnswered}
          style={{
            width: "100%",
            height: 48,
            borderRadius: 12,
            border: "none",
            background: allAnswered ? "var(--c-blue-700)" : "var(--c-line)",
            fontSize: 15,
            fontWeight: 600,
            color: allAnswered ? "#fff" : "var(--c-ink-4)",
            cursor: allAnswered ? "pointer" : "not-allowed",
          }}
        >
          {t("genericPage.next", { defaultValue: "Next" })}
        </button>
      }
    >
      <div
        style={{
          padding: "calc(20px * var(--ui-scale, 1)) 16px 0",
          display: "flex",
          flexDirection: "column",
          flex: 1,
        }}
      >
        {page.title && (
          <h2
            style={{
              fontSize: "calc(19px * var(--ui-scale, 1))",
              fontWeight: 700,
              color: "var(--c-ink)",
              margin: "0 0 calc(16px * var(--ui-scale, 1))",
            }}
          >
            {page.title}
          </h2>
        )}

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "calc(20px * var(--ui-scale, 1))",
          }}
        >
          {questions.map((q, idx) => (
            <QuestionField
              // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
              key={idx}
              question={q}
              value={answers[q.label]}
              onChange={(v) => {
                onChange(q.label, v);
                setShowValidation(false);
              }}
            />
          ))}
        </div>

        {showValidation && (
          <p role="alert" style={{ color: "var(--c-danger)", fontSize: 13, marginTop: 12 }}>
            {t("genericPage.validation", {
              defaultValue: "Please answer the required questions to continue.",
            })}
          </p>
        )}
      </div>
    </StepShell>
  );
}

interface QuestionFieldProps {
  question: FormQuestion;
  value: string | string[] | undefined;
  onChange: (value: string | string[]) => void;
}

function QuestionField({ question, value, onChange }: QuestionFieldProps) {
  const isRequired = question.required ?? false;
  const legend = (
    <span
      style={{
        fontSize: 15,
        fontWeight: 600,
        color: "var(--c-ink)",
        display: "block",
        marginBottom: 10,
      }}
    >
      {question.label}
      {isRequired ? <span style={{ color: "var(--c-danger)" }}> *</span> : null}
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
          style={{
            width: "100%",
            minHeight: 88,
            padding: "12px 14px",
            borderRadius: 12,
            border: "1.5px solid var(--c-line)",
            background: "var(--c-card)",
            fontSize: 15,
            color: "var(--c-ink)",
            outline: "none",
            resize: "vertical",
            fontFamily: "inherit",
          }}
        />
      </div>
    );
  }

  const isSingle = question.type === "single_select";
  const selected = typeof value === "string" ? value : "";
  const selectedLabels = Array.isArray(value) ? value : [];
  const maxSelect = question.max_select;

  const toggle = (optLabel: string) => {
    if (selectedLabels.includes(optLabel)) {
      onChange(selectedLabels.filter((l) => l !== optLabel));
      return;
    }
    if (maxSelect !== undefined && selectedLabels.length >= maxSelect) {
      return;
    }
    onChange([...selectedLabels, optLabel]);
  };
  const isSelected = (label: string) =>
    isSingle ? selected === label : selectedLabels.includes(label);
  const onPick = (label: string) => (isSingle ? onChange(label) : toggle(label));

  return (
    <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
      <legend style={{ padding: 0 }}>{legend}</legend>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: isSingle ? "calc(8px * var(--ui-scale, 1))" : 8,
        }}
      >
        {(question.options ?? []).map((opt, idx) => (
          <ChoiceTile
            // biome-ignore lint/suspicious/noArrayIndexKey: position is identity
            key={idx}
            selected={isSelected(opt.label)}
            onClick={() => onPick(opt.label)}
            kind={isSingle ? "radio" : "checkbox"}
            label={opt.label}
          />
        ))}
      </div>
    </fieldset>
  );
}

function ChoiceTile({
  selected,
  onClick,
  kind,
  label,
}: {
  selected: boolean;
  onClick: () => void;
  kind: "radio" | "checkbox";
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "calc(14px * var(--ui-scale, 1)) 16px",
        borderRadius: 12,
        border: `2px solid ${selected ? "var(--c-blue-700)" : "var(--c-line)"}`,
        background: selected ? "var(--c-blue-50)" : "var(--c-card)",
        fontSize: "calc(16px * var(--ui-scale, 1))",
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
          width: 20,
          height: 20,
          flexShrink: 0,
          borderRadius: kind === "radio" ? "50%" : 4,
          border: `2px solid ${selected ? "var(--c-blue-700)" : "var(--c-ink-4)"}`,
          background: selected ? "var(--c-blue-700)" : "transparent",
          display: "grid",
          placeItems: "center",
          color: "#fff",
          fontSize: 12,
          fontWeight: 700,
        }}
      >
        {selected ? (kind === "radio" ? "●" : "✓") : ""}
      </span>
      <span>{label}</span>
    </button>
  );
}
