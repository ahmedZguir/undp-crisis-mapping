import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  viewBox: "0 0 24 24",
};

export const Icon = {
  plus: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} strokeWidth={2} {...p}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  filter: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M3 5h18l-7 9v6l-4-2v-4z" />
    </svg>
  ),
  search: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-5-5" />
    </svg>
  ),
  alert: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M12 9v4M12 17h.01" />
      <path d="M10.3 3.9L2.5 17.5A2 2 0 004.2 20.5h15.6A2 2 0 0021.5 17.5L13.7 3.9a2 2 0 00-3.4 0z" />
    </svg>
  ),
  check: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M5 12.5l4.5 4.5L19 6.5" />
    </svg>
  ),
  download: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M12 3v13M6 11l6 6 6-6M5 21h14" />
    </svg>
  ),
  arrowRight: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} strokeWidth={2} {...p}>
      <path d="M9 18l6-6-6-6" />
    </svg>
  ),
  drag: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="currentColor" {...p}>
      <circle cx="9" cy="6" r="1.4" />
      <circle cx="15" cy="6" r="1.4" />
      <circle cx="9" cy="12" r="1.4" />
      <circle cx="15" cy="12" r="1.4" />
      <circle cx="9" cy="18" r="1.4" />
      <circle cx="15" cy="18" r="1.4" />
    </svg>
  ),
  edit: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18 2l4 4-12 12H6v-4z" />
    </svg>
  ),
  eye: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  camera: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M3 7h3l2-3h8l2 3h3a1 1 0 011 1v11a1 1 0 01-1 1H3a1 1 0 01-1-1V8a1 1 0 011-1z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  ),
  pin: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M12 22s8-7 8-13a8 8 0 10-16 0c0 6 8 13 8 13z" />
      <circle cx="12" cy="9" r="3" />
    </svg>
  ),
  copy: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
    </svg>
  ),
  trash: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
    </svg>
  ),
  spark: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 2l1.6 5.4L19 9l-5.4 1.6L12 16l-1.6-5.4L5 9l5.4-1.6z" />
    </svg>
  ),
  shield: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M12 2l8 3v7c0 5-3.5 9-8 10-4.5-1-8-5-8-10V5l8-3z" />
    </svg>
  ),
  mail: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M4 7l8 6 8-6" />
    </svg>
  ),
  lock: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <rect x="4" y="11" width="16" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 018 0v3" />
    </svg>
  ),
  // Marks AI/semantic surfaces, apart from the structured-filter blue.
  sparkle: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} strokeWidth={2} {...p}>
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
      <circle cx="12" cy="12" r="3.4" />
    </svg>
  ),
  // Marks the search-by-meaning surface.
  brain: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M9 2.5a2.5 2.5 0 0 0-2.46 2.03A3 3 0 0 0 4.2 9.2a3.2 3.2 0 0 0-.2 5.52A2.8 2.8 0 0 0 6.72 18.8 2.4 2.4 0 0 0 11 17.55V4.9A2.4 2.4 0 0 0 9 2.5Z" />
      <path d="M15 2.5a2.5 2.5 0 0 1 2.46 2.03A3 3 0 0 1 19.8 9.2a3.2 3.2 0 0 1 .2 5.52A2.8 2.8 0 0 1 17.28 18.8 2.4 2.4 0 0 1 13 17.55V4.9A2.4 2.4 0 0 1 15 2.5Z" />
    </svg>
  ),
  building: (p: IconProps) => (
    <svg aria-hidden="true" focusable="false" {...stroke} {...p}>
      <path d="M3 21h18M5 21V7l7-4 7 4v14" />
      <path d="M9 21v-5h6v5M9 8h.01M15 8h.01M9 12h.01M15 12h.01" />
    </svg>
  ),
};
