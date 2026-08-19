/** Palette aligned with scripts/daily_viz.py */
export const colors = {
  green: "#4CAF50",
  orange: "#FF9800",
  red: "#F44336",
  lowLine: "#E53935",
  highLine: "#E65100",
  bolus: "#1565C0",
  carb: "#FFA726",
  basalFill: "#BBDEFB",
  basalEdge: "#1E88E5",
  suspend: "#FFCDD2",
  bg: "#FAFAFA",
};

export function bgSegmentColor(
  bg: number,
  low: number,
  high: number,
): string {
  if (bg < low || bg > 250) return colors.red;
  if (bg > high) return colors.orange;
  return colors.green;
}

export type BgSegmentKind = "low" | "high" | "in-range";

/**
 * Direction relative to the target band, independent of severity. Used to
 * pick a marker *shape* (in addition to `bgSegmentColor`'s hue) so out-of-
 * range CGM points aren't distinguished by color alone — colorblind users
 * get "above/below target" from the shape even if the color is ambiguous.
 */
export function bgSegmentKind(bg: number, low: number, high: number): BgSegmentKind {
  if (bg < low) return "low";
  if (bg > high) return "high";
  return "in-range";
}

/**
 * SVG polygon `points` for an upward- or downward-pointing triangle marker
 * centered at (cx, cy) with "radius" r, matching the footprint of a circle
 * dot of the same r so the two shapes read as the same size on the chart.
 */
export function triangleDotPoints(
  cx: number,
  cy: number,
  r: number,
  direction: "up" | "down",
): string {
  if (direction === "up") {
    return `${cx},${cy - r} ${cx + r},${cy + r} ${cx - r},${cy + r}`;
  }
  return `${cx - r},${cy - r} ${cx + r},${cy - r} ${cx},${cy + r}`;
}
