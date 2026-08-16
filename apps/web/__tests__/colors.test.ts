import { describe, expect, it } from "vitest";
import { bgSegmentKind, triangleDotPoints } from "@/lib/colors";

const LOW = 70;
const HIGH = 180;

describe("bgSegmentKind", () => {
  it("is 'low' below the low target", () => {
    expect(bgSegmentKind(60, LOW, HIGH)).toBe("low");
  });

  it("is 'high' above the high target", () => {
    expect(bgSegmentKind(200, LOW, HIGH)).toBe("high");
  });

  it("is 'high' even for a very-high reading (>250) that shares a color with 'low'", () => {
    // bgSegmentColor buckets bg > 250 into the same red as bg < low, but the
    // *shape* must still say "high" (direction), not "low" — that's the
    // whole point of encoding direction separately from severity/color.
    expect(bgSegmentKind(300, LOW, HIGH)).toBe("high");
  });

  it("is 'in-range' at the band edges and midpoint", () => {
    expect(bgSegmentKind(LOW, LOW, HIGH)).toBe("in-range");
    expect(bgSegmentKind(HIGH, LOW, HIGH)).toBe("in-range");
    expect(bgSegmentKind(120, LOW, HIGH)).toBe("in-range");
  });
});

describe("triangleDotPoints", () => {
  it("returns three cx,cy pairs", () => {
    const pts = triangleDotPoints(10, 20, 4, "up").split(" ");
    expect(pts).toHaveLength(3);
    pts.forEach((p) => expect(p).toMatch(/^-?\d+(\.\d+)?,-?\d+(\.\d+)?$/));
  });

  it("points up: the apex is above (smaller y than) the base corners", () => {
    const [apex, left, right] = triangleDotPoints(10, 20, 4, "up").split(" ");
    const apexY = Number(apex.split(",")[1]);
    const leftY = Number(left.split(",")[1]);
    const rightY = Number(right.split(",")[1]);
    expect(apexY).toBeLessThan(leftY);
    expect(apexY).toBeLessThan(rightY);
  });

  it("points down: the apex is below (larger y than) the base corners", () => {
    const [left, right, apex] = triangleDotPoints(10, 20, 4, "down").split(" ");
    const leftY = Number(left.split(",")[1]);
    const rightY = Number(right.split(",")[1]);
    const apexY = Number(apex.split(",")[1]);
    expect(apexY).toBeGreaterThan(leftY);
    expect(apexY).toBeGreaterThan(rightY);
  });

  it("is centered on cx (base corners straddle it symmetrically)", () => {
    const [, right, left] = triangleDotPoints(10, 20, 4, "up").split(" ");
    const leftX = Number(left.split(",")[0]);
    const rightX = Number(right.split(",")[0]);
    expect(leftX).toBe(10 - 4);
    expect(rightX).toBe(10 + 4);
  });
});
