import {
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { useIsRtl } from "../hooks/useIsRtl";
import { StepScrollContext } from "./stepScroll";

interface StepShellProps {
  stepNumber?: number;
  totalSteps?: number;
  onBack: () => void;
  children: ReactNode;
  /** Action row pinned below the scroll area. */
  footer?: ReactNode;
}

// Overflow tolerance for the scroll gate (see recompute).
const SCROLL_GATE_SLOP_PX = 40;

export function StepShell({ stepNumber, totalSteps, onBack, children, footer }: StepShellProps) {
  const { t } = useTranslation();
  const isRtl = useIsRtl();
  const showStepper = stepNumber !== undefined && totalSteps !== undefined;

  // Scroll gate: while content overflows and the bottom has not been reached, dim and
  // disable the footer action so every option is seen before continuing.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [gated, setGated] = useState(false);
  // Latches once the bottom is reached; re-armed only if content grows past what was seen.
  const seenBottomRef = useRef(false);
  const seenHeightRef = useRef(0);

  // Scroll memory from ReportForm, keyed by stepNumber; null outside the report flow.
  const scrollStore = useContext(StepScrollContext);
  const persist = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !scrollStore || stepNumber === undefined) return;
    scrollStore.set(stepNumber, { top: el.scrollTop, seenBottom: seenBottomRef.current });
  }, [scrollStore, stepNumber]);

  const recompute = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    persist();
    // The slop ignores a few px of padding overflow and stops flicker when a selection
    // nudges card height; it also lets "near the end" count as seen.
    const hidden = el.scrollHeight - el.clientHeight;
    const overflowing = hidden > SCROLL_GATE_SLOP_PX;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_GATE_SLOP_PX;
    if (el.scrollHeight > seenHeightRef.current + SCROLL_GATE_SLOP_PX) {
      seenBottomRef.current = false;
    }
    if (atBottom) {
      seenBottomRef.current = true;
      seenHeightRef.current = el.scrollHeight;
    }
    setGated(overflowing && !atBottom && !seenBottomRef.current);
  }, [persist]);

  // Before paint, so Back lands where the user left off.
  // biome-ignore lint/correctness/useExhaustiveDependencies: run once on mount
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !scrollStore || stepNumber === undefined) return;
    const saved = scrollStore.get(stepNumber);
    if (!saved) return;
    seenBottomRef.current = saved.seenBottom;
    seenHeightRef.current = el.scrollHeight;
    el.scrollTop = saved.top;
    recompute();
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    recompute();
    el.addEventListener("scroll", recompute, { passive: true });
    // Observing the inner content catches steps that reveal extra options.
    // Guarded because jsdom has no ResizeObserver.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(recompute) : null;
    ro?.observe(el);
    if (ro && el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", recompute);
      ro?.disconnect();
    };
  }, [recompute]);

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  };

  return (
    <div
      style={{
        // Keep the pinned footer above the fixed tab bar (it publishes --undp-tabbar-h).
        height: "calc(100svh - var(--undp-tabbar-h, 0px))",
        background: "var(--c-surface)",
        fontFamily: "var(--font-ui)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding:
            "calc(10px + env(safe-area-inset-top)) calc(16px + env(safe-area-inset-right)) 10px calc(16px + env(safe-area-inset-left))",
          background: "var(--c-card)",
          borderBottom: "1px solid var(--c-line-2)",
        }}
      >
        <button
          type="button"
          aria-label={t("stepShell.back")}
          onClick={onBack}
          style={{
            width: 36,
            height: 36,
            border: 0,
            background: "transparent",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            flexShrink: 0,
            padding: 0,
          }}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 18 18"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
            style={isRtl ? { transform: "scaleX(-1)" } : undefined}
          >
            <path
              d="M11 4L6 9L11 14"
              stroke="var(--c-ink-2)"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--c-ink-3)",
            flex: 1,
            textAlign: "center",
          }}
        >
          {showStepper
            ? t("stepShell.stepOf", { current: stepNumber, total: totalSteps })
            : t("stepShell.report")}
        </span>

        <div style={{ width: 36, flexShrink: 0 }} />
      </div>

      {showStepper && (
        <div
          style={{
            flexShrink: 0,
            background: "var(--c-card)",
            padding: "10px 16px 0",
            display: "flex",
            gap: 4,
          }}
        >
          {Array.from({ length: totalSteps }).map((_, i) => (
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: stepper dots are positional
              key={i}
              style={{
                flex: 1,
                height: 3,
                borderRadius: 999,
                background: i < stepNumber ? "var(--c-blue-700)" : "var(--c-line)",
              }}
            />
          ))}
        </div>
      )}

      <div
        ref={scrollRef}
        // Lets a step lock the scroll region (e.g. while the soft keyboard is open).
        data-step-scroll=""
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          paddingLeft: "env(safe-area-inset-left)",
          paddingRight: "env(safe-area-inset-right)",
        }}
      >
        {children}
      </div>

      {footer && (
        <div
          style={{
            flexShrink: 0,
            position: "relative",
            background: "var(--c-card)",
            borderTop: "1px solid var(--c-line-2)",
            // The tab bar already reserves the safe area; subtract it to avoid a doubled gap.
            padding:
              "12px calc(16px + env(safe-area-inset-right)) calc(16px + max(0px, env(safe-area-inset-bottom) - var(--undp-tabbar-h, 0px))) calc(16px + env(safe-area-inset-left))",
          }}
        >
          {gated && (
            <button
              type="button"
              onClick={scrollToBottom}
              style={{
                position: "absolute",
                left: "50%",
                top: 0,
                transform: "translate(-50%, -100%)",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "8px 16px",
                margin: 0,
                border: "none",
                borderRadius: "999px 999px 0 0",
                background: "var(--c-blue-700)",
                color: "#fff",
                fontSize: 13,
                fontWeight: 600,
                fontFamily: "var(--font-ui)",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              <span aria-hidden="true">↓</span>
              {t("stepShell.scrollForMore")}
            </button>
          )}
          <div
            aria-hidden={gated || undefined}
            style={{
              opacity: gated ? 0.45 : 1,
              pointerEvents: gated ? "none" : "auto",
              transition: "opacity 0.2s ease",
            }}
          >
            {footer}
          </div>
        </div>
      )}
    </div>
  );
}
