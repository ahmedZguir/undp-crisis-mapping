// Planar helpers over crisis-area GeoJSON. Coordinates are [lng, lat].
import type { GeoJSONMultiPolygon, GeoJSONPolygon } from "../types/admin";

export type AreaGeometry = GeoJSONPolygon | GeoJSONMultiPolygon;

/** [[west, south], [east, north]] — assignable to MapLibre's LngLatBoundsLike. */
type LngLatBounds = [[number, number], [number, number]];

/** Bounding box of a list of polygons (each a list of rings); null when empty. */
export function boundsOfRings(polys: number[][][][]): LngLatBounds | null {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const poly of polys)
    for (const ring of poly)
      for (const [x, y] of ring) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
  if (!Number.isFinite(minX)) return null;
  return [
    [minX, minY],
    [maxX, maxY],
  ];
}

export function bboxOfGeometry(g: AreaGeometry): LngLatBounds | null {
  if (g.type === "Polygon") return boundsOfRings([g.coordinates]);
  if (g.type === "MultiPolygon") return boundsOfRings(g.coordinates);
  return null;
}

function pointInRing(lng: number, lat: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** Point-in-polygon against each polygon's outer ring (holes ignored). */
export function pointInGeometry(lng: number, lat: number, g: AreaGeometry): boolean {
  if (g.type === "Polygon") return pointInRing(lng, lat, g.coordinates[0]);
  return g.coordinates.some((poly) => pointInRing(lng, lat, poly[0]));
}
