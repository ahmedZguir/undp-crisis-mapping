/**
 * Small hover/focus tooltip for controls a coordinator might not recognise.
 * Wrap a custom trigger as `children`, or omit it for the default ⓘ dot.
 * Opens on hover and keyboard focus; closes on leave/blur/Escape.
 *
 * Portalled into the nearest `.admin-root` (so the scoped `.info-tip-pop`
 * styles apply) and positioned `fixed`, clamped to the viewport, so a
 * scrolling or `overflow: hidden` ancestor can't clip it or reflow around it.
 */
import { type ReactNode, useCallback, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface InfoTooltipProps {
  /** Popover body. */
  content: ReactNode;
  /** Custom trigger element; when omitted a small ⓘ dot is rendered. */
  children?: ReactNode;
  /** Popover horizontal alignment relative to the trigger. */
  align?: "start" | "end";
  /** Accessible label for the default ⓘ trigger. */
  triggerLabel?: string;
  /**
   * Use the card surface look (light in light mode, dark in dark mode, like
   * the dashboard KPI tooltips) instead of the default inverted high-contrast
   * popover.
   */
  surface?: boolean;
}

interface Coords {
  top: number;
  left: number;
}

// Distance between the trigger and the popover, and the minimum padding kept
// between the popover and the viewport edges when clamping.
const GAP = 7;
const VIEWPORT_MARGIN = 8;
// Fallback width matching `.info-tip-pop { width: 232px }` for the first
// measurement pass before the node is in the DOM.
const FALLBACK_WIDTH = 232;

export function InfoTooltip({
  content,
  children,
  align = "start",
  triggerLabel = "More information",
  surface = false,
}: InfoTooltipProps) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<Coords | null>(null);
  const id = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popRef = useRef<HTMLSpanElement>(null);

  const show = () => setOpen(true);
  const hide = () => setOpen(false);

  const place = useCallback(() => {
    const trigger = wrapRef.current;
    if (!trigger) return;
    const r = trigger.getBoundingClientRect();
    const pop = popRef.current;
    const popW = pop?.offsetWidth ?? FALLBACK_WIDTH;
    const popH = pop?.offsetHeight ?? 0;

    // Horizontal: anchor to the trigger's left (start) or right (end) edge,
    // then clamp so the card never leaves the viewport.
    let left = align === "end" ? r.right - popW : r.left;
    left = Math.max(VIEWPORT_MARGIN, Math.min(left, window.innerWidth - popW - VIEWPORT_MARGIN));

    // Vertical: prefer below the trigger; flip above if it would overflow the
    // bottom and there is room up top.
    let top = r.bottom + GAP;
    if (top + popH > window.innerHeight - VIEWPORT_MARGIN && r.top - GAP - popH > VIEWPORT_MARGIN) {
      top = r.top - GAP - popH;
    }
    top = Math.max(VIEWPORT_MARGIN, top);

    setCoords({ top, left });
  }, [align]);

  // Measure + position once the popover is in the DOM, and keep it anchored
  // while it stays open (scroll/resize move the trigger). Layout effect so the
  // first paint already carries the final coordinates (no flash at 0,0).
  useLayoutEffect(() => {
    if (!open) {
      setCoords(null);
      return;
    }
    place();
    const reposition = () => place();
    // `true` = capture, so we also catch scrolling on inner containers.
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, place]);

  // Portal target: the enclosing admin shell, so the `.admin-root`-scoped
  // popover CSS still matches. Falls back to <body> if not found.
  const portalTarget =
    open && wrapRef.current
      ? ((wrapRef.current.closest(".admin-root") as HTMLElement | null) ?? document.body)
      : null;

  return (
    <span
      ref={wrapRef}
      className="info-tip"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onKeyDown={(e) => {
        if (e.key === "Escape") hide();
      }}
    >
      {children ? (
        // A real button so the trigger is keyboard-focusable (focus bubbles
        // to the wrapper's onFocus); reset to inherit the child's look.
        <button
          type="button"
          aria-describedby={open ? id : undefined}
          onClick={(e) => {
            e.preventDefault();
            setOpen((v) => !v);
          }}
          style={{
            display: "inline-flex",
            border: 0,
            background: "transparent",
            padding: 0,
            font: "inherit",
            color: "inherit",
            cursor: "help",
          }}
        >
          {children}
        </button>
      ) : (
        <button
          type="button"
          className="info-tip-dot"
          aria-label={triggerLabel}
          aria-describedby={open ? id : undefined}
          onClick={(e) => {
            e.preventDefault();
            setOpen((v) => !v);
          }}
        >
          i
        </button>
      )}
      {open &&
        portalTarget &&
        createPortal(
          <span
            ref={popRef}
            role="tooltip"
            id={id}
            className={`info-tip-pop${surface ? " surface" : ""}`}
            style={{
              position: "fixed",
              top: coords?.top ?? 0,
              left: coords?.left ?? 0,
              margin: 0,
              // Hidden until measured so it can't flash at the origin.
              visibility: coords ? "visible" : "hidden",
            }}
          >
            {content}
          </span>,
          portalTarget,
        )}
    </span>
  );
}
