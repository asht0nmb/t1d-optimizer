import { describe, expect, it } from "vitest";
import {
  colorbarStops,
  colorbarTicks,
  heatmapColor,
} from "@/lib/heatmap-color";

const LOW = 70;
const HIGH = 180;

function parse(rgb: string): [number, number, number] {
  const m = rgb.match(/rgb\((\d+), (\d+), (\d+)\)/);
  if (!m) throw new Error(`not an rgb string: ${rgb}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

describe("heatmapColor", () => {
  it("returns neutral grey for null", () => {
    expect(heatmapColor(null, LOW, HIGH)).toBe("#f1f5f9");
  });

  it("clamps below Z_MIN to the deep-blue endpoint", () => {
    expect(heatmapColor(20, LOW, HIGH)).toBe("rgb(21, 101, 192)");
  });

  it("clamps above Z_MAX to the deep-red endpoint", () => {
    expect(heatmapColor(400, LOW, HIGH)).toBe("rgb(183, 28, 28)");
  });

  it("hits the in-range green near the band midpoint", () => {
    const mid = (LOW + HIGH) / 2;
    expect(heatmapColor(mid, LOW, HIGH)).toBe("rgb(67, 160, 71)");
  });

  it("warms (R > G) once BG climbs above target", () => {
    const inRange = parse(heatmapColor(120, LOW, HIGH));
    const high = parse(heatmapColor(200, LOW, HIGH));
    const veryHigh = parse(heatmapColor(280, LOW, HIGH));
    // In-range green is not red-dominant; high readings are warm/red-dominant.
    expect(inRange[0]).toBeLessThan(inRange[1]);
    expect(high[0]).toBeGreaterThan(high[1]);
    expect(veryHigh[0]).toBeGreaterThan(veryHigh[1]);
  });

  it("is bluer as BG drops below target", () => {
    const inRange = parse(heatmapColor(120, LOW, HIGH));
    const low = parse(heatmapColor(55, LOW, HIGH));
    expect(low[2]).toBeGreaterThan(inRange[2]); // more blue
  });

  it("interpolates between stops (not a hard bucket)", () => {
    // A value between low and mid should differ from both endpoints.
    const c = heatmapColor((LOW + (LOW + HIGH) / 2) / 2, LOW, HIGH);
    expect(c).not.toBe(heatmapColor(LOW, LOW, HIGH));
    expect(c).not.toBe(heatmapColor((LOW + HIGH) / 2, LOW, HIGH));
  });
});

describe("colorbarStops", () => {
  it("returns the requested number of rgb strings", () => {
    const s = colorbarStops(LOW, HIGH, 10);
    expect(s).toHaveLength(10);
    s.forEach((c) => expect(c).toMatch(/^rgb\(\d+, \d+, \d+\)$/));
  });
});

describe("colorbarTicks", () => {
  it("includes the targets and clinical reference values, sorted", () => {
    const t = colorbarTicks(LOW, HIGH);
    expect(t).toContain(LOW);
    expect(t).toContain(HIGH);
    expect(t).toContain(250);
    expect(t).toEqual([...t].sort((a, b) => a - b));
  });
});
