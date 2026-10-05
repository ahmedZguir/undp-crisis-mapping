export interface BuildingDetails {
  gers: string;
  name?: string | null;
  class?: string | null;
  subtype?: string | null;
  height?: number | null;
  num_floors?: number | null;
  min_height?: number | null;
  level?: number | null;
}

interface BuildingDetailsSheetProps {
  building: BuildingDetails | null;
}

function formatHeight(h: number | null | undefined): string | null {
  if (h === null || h === undefined) return null;
  return `${h.toFixed(1)} m`;
}

function titleCase(s: string): string {
  return s
    .split(/[_\s]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function BuildingDetailsSheet({ building }: BuildingDetailsSheetProps) {
  if (!building) return null;

  const heightStr = formatHeight(building.height);
  const minHeightStr = formatHeight(building.min_height);
  const displayName = building.name?.trim() || "Unnamed building";

  const facts: string[] = [];
  if (building.class) facts.push(titleCase(building.class));
  if (building.subtype) facts.push(titleCase(building.subtype));
  if (building.num_floors !== null && building.num_floors !== undefined) {
    const n = building.num_floors;
    facts.push(`${n} ${n === 1 ? "floor" : "floors"}`);
  }
  if (heightStr) facts.push(heightStr);
  if (minHeightStr) facts.push(`base ${minHeightStr}`);
  if (building.level !== null && building.level !== undefined) {
    facts.push(`level ${building.level}`);
  }

  return (
    <div
      aria-label="Building details"
      style={{
        position: "absolute",
        top: 10,
        left: "50%",
        transform: "translateX(-50%)",
        maxWidth: "min(280px, calc(100% - 110px))",
        zIndex: 1000,
        background: "var(--c-card)",
        borderRadius: 10,
        padding: "6px 10px",
        boxShadow: "0 2px 10px rgba(0,0,0,0.12)",
        // Map gestures pass through; the card is purely informational.
        pointerEvents: "none",
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--c-ink)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {displayName}
      </div>
      {facts.length > 0 && (
        <div
          style={{
            fontSize: 11,
            color: "var(--c-ink-3)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {facts.join(" · ")}
        </div>
      )}
    </div>
  );
}
