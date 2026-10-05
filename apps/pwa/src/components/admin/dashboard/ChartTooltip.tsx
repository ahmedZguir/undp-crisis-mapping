/**
 * Shared hover tooltip for the analytics charts.
 *
 * Charts call `tip.show(targetEl, content)` on segment hover and `tip.hide()`
 * on leave, then render `<ChartTooltip tip={tip.tip} />` once inside a wrapper
 * that carries `ref={tip.ref}`.
 *
 * Portalled into the nearest `.dash-root` rather than the chart wrapper,
 * because the scrolling analytics column would clip it. Positioning uses
 * `getBoundingClientRect` deltas against that host, so the admin shell's
 * `zoom` cancels out.
 */
import { type ReactNode, useCallback, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface ChartTipState {
  x: number;
  y: number;
  content: ReactNode;
  host: HTMLElement;
}

export function useChartTooltip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<ChartTipState | null>(null);

  const show = useCallback((target: Element, content: ReactNode) => {
    const host = ref.current?.closest<HTMLElement>(".dash-root");
    if (!host) return;
    const hostRect = host.getBoundingClientRect();
    const t = target.getBoundingClientRect();
    setTip({
      x: t.left + t.width / 2 - hostRect.left,
      y: t.top - hostRect.top,
      content,
      host,
    });
  }, []);

  const hide = useCallback(() => setTip(null), []);

  return { ref, tip, show, hide };
}

export function ChartTooltip({ tip }: { tip: ChartTipState | null }) {
  if (!tip) return null;
  return createPortal(
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }}>
      {tip.content}
    </div>,
    tip.host,
  );
}

/** A labelled count row for tooltip bodies: swatch · name · number. */
export function TipRow({ color, name, n }: { color: string; name: string; n: number }) {
  return (
    <div className="ct-row">
      <span className="ct-sw" style={{ background: color }} />
      <span>{name}</span>
      <span className="ct-n num">{n.toLocaleString()}</span>
    </div>
  );
}
