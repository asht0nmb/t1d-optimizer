/**
 * Continuous BG colorscale for the heatmap, anchored to the user's
 * low/high targets. Mirrors apps/local/charts/heatmap.py: deep blue (hypo)
 * → green (in-range) → orange/red (hyper), so the same reading reads the
 * same colour in both shells.
 */

const Z_MIN = 40;
const Z_MAX = 320;

interface Stop {
  /** normalized position in [0, 1] */
  pos: number;
  rgb: [number, number, number];
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/** Normalize a BG value into [0, 1] across the fixed [40, 320] z-range. */
function norm(value: number): number {
  return Math.min(1, Math.max(0, (value - Z_MIN) / (Z_MAX - Z_MIN)));
}

/**
 * Ordered colour stops keyed to the low/high targets. The midpoint of the
 * in-range band is the deepest green; below `low` ramps to blue, above
 * `high` ramps through amber to red.
 */
function stops(low: number, high: number): Stop[] {
  const mid = (low + high) / 2;
  const raw: Array<[number, string]> = [
    [Z_MIN, "#1565C0"],
    [Math.max(Z_MIN + 1, low - 1), "#42A5F5"],
    [low, "#81C784"],
    [mid, "#43A047"],
    [high, "#FFB300"],
    [Math.min(Z_MAX - 1, 250), "#F4511E"],
    [Z_MAX, "#B71C1C"],
  ];
  return raw
    .map(([v, hex]) => ({ pos: norm(v), rgb: hexToRgb(hex) }))
    .sort((a, b) => a.pos - b.pos);
}

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

/**
 * Interpolated `rgb(...)` colour for a BG value on the anchored colorscale.
 * Null/undefined → a neutral "no data" grey.
 */
export function heatmapColor(
  value: number | null | undefined,
  low: number,
  high: number,
): string {
  if (value == null || Number.isNaN(value)) return "#f1f5f9";
  const p = norm(value);
  const sc = stops(low, high);

  if (p <= sc[0].pos) return rgbStr(sc[0].rgb);
  if (p >= sc[sc.length - 1].pos) return rgbStr(sc[sc.length - 1].rgb);

  for (let i = 0; i < sc.length - 1; i++) {
    const a = sc[i];
    const b = sc[i + 1];
    if (p >= a.pos && p <= b.pos) {
      const span = b.pos - a.pos;
      const t = span === 0 ? 0 : (p - a.pos) / span;
      return rgbStr([
        lerp(a.rgb[0], b.rgb[0], t),
        lerp(a.rgb[1], b.rgb[1], t),
        lerp(a.rgb[2], b.rgb[2], t),
      ]);
    }
  }
  return rgbStr(sc[sc.length - 1].rgb);
}

function rgbStr(rgb: [number, number, number]): string {
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

/**
 * Evenly-spaced sample colours spanning the z-range, for rendering a
 * continuous colorbar gradient. Returns `steps` `rgb(...)` strings from
 * Z_MIN to Z_MAX.
 */
export function colorbarStops(
  low: number,
  high: number,
  steps = 24,
): string[] {
  const out: string[] = [];
  for (let i = 0; i < steps; i++) {
    const v = Z_MIN + (i / (steps - 1)) * (Z_MAX - Z_MIN);
    out.push(heatmapColor(v, low, high));
  }
  return out;
}

/** Reference tick values (mg/dL) for the colorbar, deduped + sorted. */
export function colorbarTicks(low: number, high: number): number[] {
  const mid = Math.round((low + high) / 2);
  return Array.from(new Set([60, low, mid, high, 250, 300]))
    .filter((v) => v >= Z_MIN && v <= Z_MAX)
    .sort((a, b) => a - b);
}

export const HEATMAP_Z_MIN = Z_MIN;
export const HEATMAP_Z_MAX = Z_MAX;
