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
