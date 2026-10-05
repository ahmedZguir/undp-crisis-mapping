// Crisis-type line icons keyed by the exact `crisis.type` strings (label ===
// value); unknown/custom types fall back to a map-pin. Keep in sync with the
// crisis-type catalogue in CrisesPage.
export const CRISIS_TYPE_ICONS: Record<string, string> = {
  Earthquake: "M3 12h3l2-6 4 13 3-9 2 5h4",
  Flood:
    "M3 16c2 0 2 1.6 4 1.6S9 16 11 16s2 1.6 4 1.6S17 16 19 16M3 11c2 0 2 1.6 4 1.6S9 11 11 11s2 1.6 4 1.6S17 11 19 11",
  Tsunami: "M3 18c4 0 4-10 9-10 3 0 4 3 7 3M5 18c0-3 2-5 4-5M3 21h18",
  Hurricane: "M4 7h13a3 3 0 1 0-3-3M4 12h16M5 17h10a3 3 0 1 1-3 3",
  Landslide: "M3 20h18L14 8l-3 4-2-3-6 11Z",
  Wildfire:
    "M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z",
  Explosion:
    "M12 2l1.8 4.5L18 4l-1.5 4.5L21 9l-4 2.2L20 15l-4.5-1L14 20l-2-4-2 4-1.5-5L4 15l3-3.8L3 9l4.5-.5L6 4l4.2 2.5L12 2Z",
  "Chemical incident": "M9 3h6M10 3v5l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 17l-5-9V3M7.5 14h9",
  Conflict:
    "M14.5 17.5 3 6 3 3 6 3 17.5 14.5M13 19 19 13M16 16 20 20M19 21 21 19M14.5 6.5 18 3 21 3 21 6 17.5 9.5M5 14 9 18M7 17 4 20M3 19 5 21",
  "Civil unrest": "M3 11l18-5v12L3 14v-3zM11.6 16.8a3 3 0 1 1-5.8-1.6",
};

export const CRISIS_PIN_PATH = "M12 21s-6-5.7-6-10a6 6 0 0 1 12 0c0 4.3-6 10-6 10Z";

// Without `size` the SVG has no width/height, so the surrounding `.ic` CSS sizes it.
export function CrisisGlyph({
  type,
  size,
  strokeWidth = 1.8,
}: {
  type?: string | null;
  size?: number;
  strokeWidth?: number;
}) {
  const d = (type && CRISIS_TYPE_ICONS[type]) || CRISIS_PIN_PATH;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}
