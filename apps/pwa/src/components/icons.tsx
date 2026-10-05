import { useIsRtl } from "../hooks/useIsRtl";

// 16px "forward" arrow; mirrored in RTL unless `flipInRtl` is false.
export function ArrowRightIcon({ flipInRtl = true }: { flipInRtl?: boolean }) {
  const isRtl = useIsRtl();
  return (
    <svg
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      style={flipInRtl && isRtl ? { transform: "scaleX(-1)" } : undefined}
    >
      <path
        d="M3 8h10M9 4l4 4-4 4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
