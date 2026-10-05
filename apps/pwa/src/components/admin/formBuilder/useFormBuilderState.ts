// Reducer-backed builder state. Selection and `dirty` live alongside the
// schema so they stay coherent with it.
//
// Addressing is by position (page, question and option indices); the schema
// has no ids.
//
// Invariants enforced in the reducer, not the UI:
//   * `photo_and_damage` stays at index 0 and `location` at 1 (both locked);
//     reorder and `enabled` actions on locked pages are no-ops.
//   * Other pages move freely within indices ≥ 2.
import { useReducer } from "react";
import type {
  FormOption,
  FormPage,
  FormPageKind,
  FormQuestion,
  FormQuestionType,
  FormSchema,
} from "../../../types";

export type Selection =
  | { kind: "page"; pageIdx: number }
  | { kind: "question"; pageIdx: number; questionIdx: number }
  | null;

export interface BuilderState {
  baseVersion: number;
  initialSchema: FormSchema;
  schema: FormSchema;
  // Stable per-page id, parallel to `schema.pages`, so React keys survive
  // mid-drag reorders (an unmounted dragged node cancels the browser drag).
  pageIds: string[];
  dirty: boolean;
  selection: Selection;
}

let _pageIdCounter = 0;
function nextPageId(): string {
  _pageIdCounter += 1;
  return `p${_pageIdCounter}`;
}

export type BuilderAction =
  | { type: "load"; baseVersion: number; schema: FormSchema }
  | { type: "togglePage"; pageIdx: number }
  | { type: "togglePageRequired"; pageIdx: number }
  | { type: "movePage"; pageIdx: number; direction: -1 | 1 }
  | { type: "addGenericPage"; afterIdx: number }
  | { type: "removePage"; pageIdx: number }
  | { type: "editPageTitle"; pageIdx: number; title: string }
  | { type: "addQuestion"; pageIdx: number; qType: FormQuestionType }
  | { type: "removeQuestion"; pageIdx: number; questionIdx: number }
  | { type: "moveQuestion"; pageIdx: number; from: number; to: number }
  | { type: "reorderPages"; from: number; to: number }
  | {
      type: "editQuestion";
      pageIdx: number;
      questionIdx: number;
      patch: Partial<Omit<FormQuestion, "options">>;
    }
  | { type: "addOption"; pageIdx: number; questionIdx: number }
  | { type: "removeOption"; pageIdx: number; questionIdx: number; optionIdx: number }
  | {
      type: "editOption";
      pageIdx: number;
      questionIdx: number;
      optionIdx: number;
      label: string;
    }
  | { type: "select"; selection: Selection }
  | { type: "markClean" };

function defaultQuestion(qType: FormQuestionType): FormQuestion {
  const base: FormQuestion = {
    type: qType,
    label: "New question",
  };
  if (qType === "free_text") return base;
  return { ...base, options: [{ label: "Option 1" }] };
}

function defaultGenericPage(): FormPage {
  return {
    kind: "generic",
    enabled: true,
    locked: false,
    title: "New page",
    questions: [defaultQuestion("single_select")],
  };
}

function isLockedKind(kind: FormPageKind): boolean {
  return kind === "photo_and_damage" || kind === "location";
}

// Built-in select pages asked and submitted on every channel (PWA, WhatsApp,
// SMS); only these can be made enabled-but-optional. Excluded: locked pages
// (they carry the minimum-content rule), `description`, `generic` pages
// (per-question `required`), and the check-in kinds (electricity,
// health_services, pressing_needs), which are dropped at submit.
const REQUIRED_TOGGLE_KINDS: ReadonlySet<FormPageKind> = new Set([
  "debris",
  "infra_type",
  "crisis_nature",
]);

function canToggleRequired(kind: FormPageKind): boolean {
  return REQUIRED_TOGGLE_KINDS.has(kind);
}

function replacePage(pages: FormPage[], idx: number, page: FormPage): FormPage[] {
  const out = pages.slice();
  out[idx] = page;
  return out;
}

function patchQuestion(
  pages: FormPage[],
  pageIdx: number,
  questionIdx: number,
  updater: (q: FormQuestion) => FormQuestion,
): FormPage[] {
  const page = pages[pageIdx];
  if (!page || page.kind !== "generic") return pages;
  const qs = (page.questions ?? []).slice();
  if (questionIdx < 0 || questionIdx >= qs.length) return pages;
  qs[questionIdx] = updater(qs[questionIdx]);
  return replacePage(pages, pageIdx, { ...page, questions: qs });
}

function reducer(state: BuilderState, action: BuilderAction): BuilderState {
  switch (action.type) {
    case "load":
      return {
        baseVersion: action.baseVersion,
        initialSchema: action.schema,
        schema: action.schema,
        pageIds: action.schema.pages.map(() => nextPageId()),
        dirty: false,
        selection: null,
      };

    case "markClean":
      return { ...state, dirty: false, initialSchema: state.schema };

    case "select":
      return { ...state, selection: action.selection };

    case "togglePage": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page || page.locked) return state;
      const next = replacePage(pages, action.pageIdx, { ...page, enabled: !page.enabled });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }

    case "togglePageRequired": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page || !canToggleRequired(page.kind)) return state;
      // Absent `required` means required (the default), so the first toggle
      // turns it off.
      const current = page.required ?? true;
      const next = replacePage(pages, action.pageIdx, { ...page, required: !current });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }

    case "movePage": {
      const pages = state.schema.pages;
      const i = action.pageIdx;
      const j = i + action.direction;
      if (i < 2 || j < 2 || j >= pages.length) return state;
      if (pages[i].locked || pages[j].locked) return state;
      const next = pages.slice();
      [next[i], next[j]] = [next[j], next[i]];
      const ids = state.pageIds.slice();
      [ids[i], ids[j]] = [ids[j], ids[i]];
      // Keep the selection on the same item across the swap.
      let selection = state.selection;
      if (selection) {
        if (selection.pageIdx === i) selection = { ...selection, pageIdx: j };
        else if (selection.pageIdx === j) selection = { ...selection, pageIdx: i };
      }
      return {
        ...state,
        schema: { ...state.schema, pages: next },
        pageIds: ids,
        dirty: true,
        selection,
      };
    }

    case "reorderPages": {
      const pages = state.schema.pages;
      const { from, to } = action;
      if (from < 2 || from >= pages.length) return state;
      if (to < 2 || to >= pages.length) return state;
      if (from === to) return state;
      if (pages[from].locked) return state;
      const next = pages.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      const ids = state.pageIds.slice();
      const [movedId] = ids.splice(from, 1);
      ids.splice(to, 0, movedId);
      let selection = state.selection;
      if (selection?.pageIdx === from) selection = { ...selection, pageIdx: to };
      return {
        ...state,
        schema: { ...state.schema, pages: next },
        pageIds: ids,
        dirty: true,
        selection,
      };
    }

    case "moveQuestion": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page || page.kind !== "generic") return state;
      const questions = (page.questions ?? []).slice();
      const { from, to } = action;
      if (from < 0 || from >= questions.length) return state;
      if (to < 0 || to >= questions.length) return state;
      if (from === to) return state;
      const [moved] = questions.splice(from, 1);
      questions.splice(to, 0, moved);
      const next = replacePage(pages, action.pageIdx, { ...page, questions });
      let selection = state.selection;
      if (
        selection?.kind === "question" &&
        selection.pageIdx === action.pageIdx &&
        selection.questionIdx === from
      ) {
        selection = { ...selection, questionIdx: to };
      }
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true, selection };
    }

    case "addGenericPage": {
      const pages = state.schema.pages;
      const at = Math.max(action.afterIdx + 1, 2);
      const next = pages.slice();
      next.splice(at, 0, defaultGenericPage());
      const ids = state.pageIds.slice();
      ids.splice(at, 0, nextPageId());
      return {
        ...state,
        schema: { ...state.schema, pages: next },
        pageIds: ids,
        dirty: true,
        selection: { kind: "page", pageIdx: at },
      };
    }

    case "removePage": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page) return state;
      if (page.locked) return state;
      if (page.kind !== "generic") return state;
      const next = pages.slice();
      next.splice(action.pageIdx, 1);
      const ids = state.pageIds.slice();
      ids.splice(action.pageIdx, 1);
      return {
        ...state,
        schema: { ...state.schema, pages: next },
        pageIds: ids,
        dirty: true,
        selection: null,
      };
    }

    case "editPageTitle": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page || page.kind !== "generic") return state;
      const next = replacePage(pages, action.pageIdx, { ...page, title: action.title });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }

    case "addQuestion": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page || page.kind !== "generic") return state;
      const newQuestions = [...(page.questions ?? []), defaultQuestion(action.qType)];
      const next = replacePage(pages, action.pageIdx, {
        ...page,
        questions: newQuestions,
      });
      return {
        ...state,
        schema: { ...state.schema, pages: next },
        dirty: true,
        selection: {
          kind: "question",
          pageIdx: action.pageIdx,
          questionIdx: newQuestions.length - 1,
        },
      };
    }

    case "removeQuestion": {
      const pages = state.schema.pages;
      const page = pages[action.pageIdx];
      if (!page || page.kind !== "generic") return state;
      const questions = (page.questions ?? []).slice();
      if (action.questionIdx < 0 || action.questionIdx >= questions.length) return state;
      // Validation requires ≥1 question per generic page; refuse to drop the last.
      if (questions.length <= 1) return state;
      questions.splice(action.questionIdx, 1);
      const next = replacePage(pages, action.pageIdx, { ...page, questions });
      const selection =
        state.selection?.kind === "question" &&
        state.selection.pageIdx === action.pageIdx &&
        state.selection.questionIdx === action.questionIdx
          ? null
          : state.selection;
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true, selection };
    }

    case "editQuestion": {
      const next = patchQuestion(state.schema.pages, action.pageIdx, action.questionIdx, (q) => {
        const patched: FormQuestion = { ...q, ...action.patch };
        // free_text drops options; a select type with none gets one seeded.
        if (action.patch.type) {
          if (action.patch.type === "free_text") {
            const { options: _opts, max_select: _max, ...rest } = patched;
            void _opts;
            void _max;
            return rest as FormQuestion;
          }
          if (!patched.options || patched.options.length === 0) {
            patched.options = [{ label: "Option 1" }];
          }
          if (action.patch.type === "single_select") {
            const { max_select: _max, ...rest } = patched;
            void _max;
            return rest as FormQuestion;
          }
        }
        return patched;
      });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }

    case "addOption": {
      const next = patchQuestion(state.schema.pages, action.pageIdx, action.questionIdx, (q) => {
        if (q.type === "free_text") return q;
        const options: FormOption[] = [
          ...(q.options ?? []),
          { label: `Option ${(q.options?.length ?? 0) + 1}` },
        ];
        return { ...q, options };
      });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }

    case "removeOption": {
      const next = patchQuestion(state.schema.pages, action.pageIdx, action.questionIdx, (q) => {
        if (q.type === "free_text") return q;
        const options = (q.options ?? []).slice();
        if (action.optionIdx < 0 || action.optionIdx >= options.length) return q;
        // Validation requires ≥1 option per select question.
        if (options.length <= 1) return q;
        options.splice(action.optionIdx, 1);
        const patched: FormQuestion = { ...q, options };
        if (typeof patched.max_select === "number" && patched.max_select > options.length) {
          patched.max_select = options.length;
        }
        return patched;
      });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }

    case "editOption": {
      const next = patchQuestion(state.schema.pages, action.pageIdx, action.questionIdx, (q) => {
        if (q.type === "free_text") return q;
        const options = (q.options ?? []).slice();
        if (action.optionIdx < 0 || action.optionIdx >= options.length) return q;
        options[action.optionIdx] = { ...options[action.optionIdx], label: action.label };
        return { ...q, options };
      });
      return { ...state, schema: { ...state.schema, pages: next }, dirty: true };
    }
  }
}

const EMPTY_INITIAL: BuilderState = {
  baseVersion: 0,
  initialSchema: { pages: [] },
  schema: { pages: [] },
  pageIds: [],
  dirty: false,
  selection: null,
};

export function useFormBuilderState(initialSchema?: FormSchema) {
  const seeded: BuilderState = initialSchema
    ? {
        baseVersion: 0,
        initialSchema,
        schema: initialSchema,
        pageIds: initialSchema.pages.map(() => nextPageId()),
        dirty: false,
        selection: null,
      }
    : EMPTY_INITIAL;
  const [state, dispatch] = useReducer(reducer, seeded);
  return { state, dispatch };
}

export type Dispatch = (action: BuilderAction) => void;

export { isLockedKind, canToggleRequired };
