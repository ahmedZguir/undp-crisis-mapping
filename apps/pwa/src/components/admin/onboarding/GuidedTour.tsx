import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

// Dependency-free coachmark engine for the admin walkthroughs. A step points at
// an element via `data-tour="<key>"`; we measure it and render a spotlight
// cutout plus an explainer card. In-house so it stays inside `.admin-root`,
// where the admin tokens are scoped.
//
// `onEnter` runs a step side effect (apply a filter, zoom the map) and may
// return a cleanup run when the step is left or the tour closes. `freeform`
// drops the spotlight so the whole page stays visible for live-demo steps.

export interface TourStep {
  /** `data-tour` value of the element to spotlight. Omit for a centered,
   *  target-less step (intro / outro). */
  target?: string;
  /** Small accent above the title: a chapter label or "Live demo". */
  tag?: string;
  title: string;
  body: string;
  /** No spotlight: the scrim clears and the card pins to the bottom so the
   *  whole page stays visible (live-demo steps). */
  freeform?: boolean;
  /** On a `freeform` step, rings this `data-tour` element (or each of several)
   *  without dimming the rest of the page. */
  highlight?: string | string[];
  /** Side effect run when the step becomes active. A returned function runs
   *  when the step is left or the tour closes, to revert the demo. */
  onEnter?: () => (() => void) | undefined;
}

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

function readBox(target: string): Box | null {
  const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

interface CardPos {
  top: number;
  left: number;
}

const CARD_W = 340;
const GAP = 14;
const PAD = 8; // spotlight padding around the target

export function GuidedTour({
  open,
  steps,
  chapter,
  doneLabel = "Done",
  onClose,
}: {
  open: boolean;
  steps: TourStep[];
  /** Faint chapter label shown in the card's meta row. */
  chapter?: string;
  /** Label for the final-step button. Defaults to "Done"; a chapter that hands
   *  off to another overrides it. */
  doneLabel?: string;
  /** Fires when the tour closes. `completed` is true only when the user
   *  reached the end and pressed Done; false for Skip / Esc / ×. */
  onClose: (completed: boolean) => void;
}) {
  const [i, setI] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  // Extra freeform rings beyond the first (the first is tracked via `box`).
  const [extraBoxes, setExtraBoxes] = useState<Box[]>([]);
  const [cardPos, setCardPos] = useState<CardPos | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  // Read inside index-keyed effects so a fresh `steps` array identity (rebuilt
  // on each parent render) never re-fires a step's side effect.
  const stepsRef = useRef(steps);
  stepsRef.current = steps;

  const step = steps[i];
  const last = i === steps.length - 1;
  const freeform = !!step?.freeform;
  // A freeform step may ring one element or several. Normalise to a list.
  const highlightList =
    freeform && step?.highlight
      ? Array.isArray(step.highlight)
        ? step.highlight
        : [step.highlight]
      : [];
  // The element to track for scroll + card anchoring: the spotlight target on a
  // normal step, or the first ring on a freeform step. Any further freeform
  // rings are measured separately (`extraBoxes` below).
  const anchor = freeform ? highlightList[0] : step?.target;
  const extraKey = highlightList.slice(1).join("|");

  // Restart at the first step each time the tour (re)opens.
  useEffect(() => {
    if (open) setI(0);
  }, [open]);

  // Clamp the index if the step list shrinks (an optional step drops out).
  useEffect(() => {
    if (i > steps.length - 1) setI(Math.max(0, steps.length - 1));
  }, [i, steps.length]);

  // Run the active step's side effect on entry; run its returned cleanup on
  // exit / close. Keyed on the index only.
  useEffect(() => {
    if (!open) return;
    const s = stepsRef.current[i];
    let cleanup: (() => void) | undefined;
    if (s?.onEnter) cleanup = s.onEnter();
    return () => {
      if (typeof cleanup === "function") cleanup();
    };
  }, [open, i]);

  // Keep the spotlight glued to its target across step changes, scrolling and
  // resizes, including anchors that mount a beat late.
  useLayoutEffect(() => {
    if (!open) return;
    const target = anchor;
    if (!target) {
      setBox(null);
      return;
    }
    // Scroll on the anchor's first appearance (not just step entry), so a step
    // whose `onEnter` reveals its own target still scrolls to it once mounted.
    let scrolled = false;
    let lastKey = "";
    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
      if (el && !scrolled) {
        scrolled = true;
        const r = el.getBoundingClientRect();
        const vh = window.innerHeight;
        const margin = 48;
        // Scroll when the anchor is off-screen or clipped at the bottom fold,
        // but leave an in-view top-pinned element (the sticky topbar nav)
        // alone. Instant, not smooth, so the same-tick `readBox` lands at the
        // final position. The target may live in a nested scroller, so reason
        // about its viewport rect, not the window. Anything taller than ~70%
        // of the viewport aligns to its top instead of centering.
        const fits = r.height <= vh * 0.7;
        // Arriving from a lower step can leave the target's top scrolled off
        // the view (negative rect top), so the cutout would cover the chrome
        // above. Top near zero but not negative (sticky nav) is left alone.
        const topAbove = r.top < 0;
        const topBelowFold = r.top > vh - margin;
        // A target that fits in the viewport but is cut off at the bottom.
        const bottomClipped = fits && r.bottom > vh - margin;
        if (topAbove || topBelowFold || bottomClipped) {
          // Tall targets align to their top so the spotlight starts at the
          // section header; ones that fit center for a balanced frame.
          el.scrollIntoView({ block: fits ? "center" : "start", behavior: "auto" });
        }
      }
      const b = readBox(target);
      // Commit only when the rect actually moved, so the steady re-measure
      // below is free when nothing changes.
      const key = b
        ? `${Math.round(b.top)},${Math.round(b.left)},${Math.round(b.width)},${Math.round(b.height)}`
        : "null";
      if (key !== lastKey) {
        lastKey = key;
        setBox(b);
      }
    };
    measure();
    // A steady cadence (not fixed timeouts) so the spotlight re-glues after
    // async content shifts the target, plus scroll/resize for immediate tracking.
    const iv = window.setInterval(measure, 160);
    window.addEventListener("resize", measure);
    // Capture phase so we also catch scrolls inside nested scrollers.
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearInterval(iv);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, anchor]);

  // Track any freeform rings beyond the first on the same cadence. No
  // scroll-into-view here: freeform steps keep the whole page in view, and the
  // first ring's effect already scrolls if needed.
  useLayoutEffect(() => {
    if (!open || !extraKey) {
      setExtraBoxes([]);
      return;
    }
    const targets = extraKey.split("|");
    const measure = () => {
      setExtraBoxes(targets.map(readBox).filter((b): b is Box => b !== null));
    };
    measure();
    const iv = window.setInterval(measure, 160);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearInterval(iv);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, extraKey]);

  // Position the card. Freeform → pinned bottom-center. Targeted → below the
  // box when there's room, else above; centered when there's no measurable
  // box. Always clamped to the viewport.
  useLayoutEffect(() => {
    if (!open) return;
    const ch = cardRef.current?.offsetHeight ?? 180;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (freeform) {
      setCardPos({ top: Math.round(vh - ch - 30), left: Math.round(vw / 2 - CARD_W / 2) });
      return;
    }
    if (!box) {
      setCardPos({ top: Math.round(vh / 2 - ch / 2), left: Math.round(vw / 2 - CARD_W / 2) });
      return;
    }
    if (box.height > vh * 0.6) {
      // Tall target (e.g. a full-height rail): place the card beside it,
      // vertically centered, rather than over the thing it describes.
      const rightLeft = box.left + box.width + GAP;
      const leftLeft = box.left - GAP - CARD_W;
      const left = rightLeft + CARD_W + GAP <= vw ? rightLeft : Math.max(GAP, leftLeft);
      setCardPos({ top: Math.round(vh / 2 - ch / 2), left: Math.round(left) });
      return;
    }
    const below = box.top + box.height + GAP;
    const top = below + ch + GAP <= vh ? below : Math.max(GAP, box.top - GAP - ch);
    let left = box.left + box.width / 2 - CARD_W / 2;
    left = Math.max(GAP, Math.min(left, vw - CARD_W - GAP));
    setCardPos({
      top: Math.round(Math.max(GAP, Math.min(top, vh - ch - GAP))),
      left: Math.round(left),
    });
  }, [box, open, freeform]);

  const goNext = useCallback(() => {
    // Resolve "is this the last step?" in the handler, not inside the setI
    // updater: calling onClose (a parent setState) from a reducer runs it
    // during render and trips React's "setState while rendering" warning.
    if (i >= steps.length - 1) onClose(true);
    else setI((n) => Math.min(n + 1, steps.length - 1));
  }, [i, steps.length, onClose]);
  const goBack = useCallback(() => setI((n) => Math.max(n - 1, 0)), []);
  const skip = useCallback(() => onClose(false), [onClose]);

  // Keyboard: Esc skips, arrows / Enter navigate.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") skip();
      else if (e.key === "ArrowRight" || e.key === "Enter") goNext();
      else if (e.key === "ArrowLeft") goBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, skip, goNext, goBack]);

  if (!open || !step) return null;

  const blank = !freeform && !box;
  const manySteps = steps.length > 9;
  // A single-step tour shows no progress: "Step 1 of 1" and a lone dot read as
  // broken.
  const showProgress = steps.length > 1;

  return (
    <div
      className={`tour-overlay${freeform ? " is-freeform" : ""}${blank ? " is-blank" : ""}`}
      role="presentation"
    >
      {box && !freeform && (
        <div
          className="tour-spot"
          style={{
            top: box.top - PAD,
            left: box.left - PAD,
            width: box.width + PAD * 2,
            height: box.height + PAD * 2,
          }}
        />
      )}
      {freeform &&
        // Freeform demo step: rings only, no scrim, so the whole dashboard stays
        // visible while every region that responds is clearly called out.
        [...(box ? [box] : []), ...extraBoxes].map((b, n) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: fixed-order rings for the active step; remounting per step replays them anyway.
            key={`ring-${n}`}
            className="tour-ring"
            style={{
              top: b.top - PAD,
              left: b.left - PAD,
              width: b.width + PAD * 2,
              height: b.height + PAD * 2,
            }}
          />
        ))}
      <div
        // Remount per step so the entrance animation replays at the new spot.
        key={i}
        ref={cardRef}
        className="tour-card"
        // biome-ignore lint/a11y/useSemanticElements: a floating, viewport-positioned coachmark is not a native <dialog>.
        role="dialog"
        aria-modal="true"
        aria-label={step.title}
        style={{
          width: CARD_W,
          top: cardPos?.top ?? -9999,
          left: cardPos?.left ?? -9999,
        }}
      >
        <div className="tour-meta">
          <span className="tour-count">
            {chapter ?? ""}
            {showProgress ? `${chapter ? " · " : ""}Step ${i + 1} of ${steps.length}` : ""}
          </span>
          <button type="button" className="tour-x" onClick={skip} aria-label="Close walkthrough">
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            >
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        {step.tag && <span className="tour-tag">{step.tag}</span>}
        <h3 className="tour-title">{step.title}</h3>
        <p className="tour-body">{step.body}</p>
        {!showProgress ? null : manySteps ? (
          <div className="tour-bar" aria-hidden="true">
            <i style={{ width: `${((i + 1) / steps.length) * 100}%` }} />
          </div>
        ) : (
          <div className="tour-dots" aria-hidden="true">
            {steps.map((_, n) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: decorative, fixed-length progress dots that never reorder.
              <span key={`dot-${n}`} className={`tour-dot${n === i ? " on" : ""}`} />
            ))}
          </div>
        )}
        <div className="tour-actions">
          <button type="button" className="tour-skip" onClick={skip}>
            Skip tour
          </button>
          <div className="tour-nav">
            {i > 0 && (
              <button type="button" className="btn secondary" onClick={goBack}>
                Back
              </button>
            )}
            <button type="button" className="btn" onClick={goNext}>
              {last ? doneLabel : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
