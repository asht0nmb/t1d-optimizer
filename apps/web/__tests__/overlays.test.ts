import { describe, expect, it } from "vitest";
import {
  clipIntervalToWindow,
  snapIntervalToTimestamps,
} from "@/lib/overlays";

const W_START = "2026-06-10T00:00:00.000Z";
const W_END = "2026-06-11T00:00:00.000Z";

describe("clipIntervalToWindow", () => {
  it("keeps an interval fully inside the window", () => {
    expect(
      clipIntervalToWindow(
        "2026-06-10T03:00:00.000Z",
        "2026-06-10T04:00:00.000Z",
        W_START,
        W_END,
      ),
    ).toEqual({
      start: "2026-06-10T03:00:00.000Z",
      end: "2026-06-10T04:00:00.000Z",
    });
  });

  it("clips an interval that spills over the window edges", () => {
    expect(
      clipIntervalToWindow(
        "2026-06-09T22:00:00.000Z",
        "2026-06-10T02:00:00.000Z",
        W_START,
        W_END,
      ),
    ).toEqual({ start: W_START, end: "2026-06-10T02:00:00.000Z" });
    expect(
      clipIntervalToWindow(
        "2026-06-10T23:00:00.000Z",
        "2026-06-11T05:00:00.000Z",
        W_START,
        W_END,
      ),
    ).toEqual({ start: "2026-06-10T23:00:00.000Z", end: W_END });
  });

  it("returns null for intervals entirely outside the window", () => {
    expect(
      clipIntervalToWindow(
        "2026-06-09T01:00:00Z",
        "2026-06-09T02:00:00Z",
        W_START,
        W_END,
      ),
    ).toBeNull();
    expect(
      clipIntervalToWindow(
        "2026-06-12T01:00:00Z",
        "2026-06-12T02:00:00Z",
        W_START,
        W_END,
      ),
    ).toBeNull();
  });

  it("returns null for empty, inverted, or unparseable intervals", () => {
    expect(
      clipIntervalToWindow(
        "2026-06-10T05:00:00Z",
        "2026-06-10T05:00:00Z",
        W_START,
        W_END,
      ),
    ).toBeNull();
    expect(
      clipIntervalToWindow(
        "2026-06-10T06:00:00Z",
        "2026-06-10T05:00:00Z",
        W_START,
        W_END,
      ),
    ).toBeNull();
    expect(clipIntervalToWindow("garbage", W_END, W_START, W_END)).toBeNull();
  });
});

describe("snapIntervalToTimestamps", () => {
  const ts = [
    "2026-06-10T01:00:00Z",
    "2026-06-10T01:05:00Z",
    "2026-06-10T02:00:00Z",
    "2026-06-10T02:05:00Z",
  ];

  it("brackets a gap with the surrounding readings", () => {
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-10T01:05:00Z", end: "2026-06-10T02:00:00Z" },
        ts,
      ),
    ).toEqual({ x1: "2026-06-10T01:05:00Z", x2: "2026-06-10T02:00:00Z" });
  });

  it("snaps interior endpoints outward to the nearest readings", () => {
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-10T01:07:00Z", end: "2026-06-10T01:58:00Z" },
        ts,
      ),
    ).toEqual({ x1: "2026-06-10T01:05:00Z", x2: "2026-06-10T02:00:00Z" });
  });

  it("falls back to first/last for edge-spilling intervals", () => {
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-10T00:00:00Z", end: "2026-06-10T01:02:00Z" },
        ts,
      ),
    ).toEqual({ x1: "2026-06-10T01:00:00Z", x2: "2026-06-10T01:05:00Z" });
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-10T02:03:00Z", end: "2026-06-10T03:00:00Z" },
        ts,
      ),
    ).toEqual({ x1: "2026-06-10T02:00:00Z", x2: "2026-06-10T02:05:00Z" });
  });

  it("returns null when there is no data or zero snapped width", () => {
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-10T01:00:00Z", end: "2026-06-10T02:00:00Z" },
        [],
      ),
    ).toBeNull();
    // Entirely after the data: both anchors collapse to the last reading.
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-10T05:00:00Z", end: "2026-06-10T06:00:00Z" },
        ts,
      ),
    ).toBeNull();
    // Entirely before the data: both anchors collapse to the first reading.
    expect(
      snapIntervalToTimestamps(
        { start: "2026-06-09T20:00:00Z", end: "2026-06-09T21:00:00Z" },
        ts,
      ),
    ).toBeNull();
  });
});
