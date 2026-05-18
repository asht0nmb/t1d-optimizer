import { describe, expect, it } from "vitest";
import {
  dayWindowUtc,
  isValidDateParam,
  todayInTimezone,
} from "@/lib/dates";

describe("isValidDateParam", () => {
  it("accepts valid ISO dates", () => {
    expect(isValidDateParam("2026-04-14")).toBe(true);
  });
  it("rejects invalid dates", () => {
    expect(isValidDateParam("2026-13-40")).toBe(false);
    expect(isValidDateParam("04-14-2026")).toBe(false);
  });
});

describe("dayWindowUtc", () => {
  it("returns half-open interval in America/Los_Angeles", () => {
    const { since, until } = dayWindowUtc("2026-04-14", "America/Los_Angeles");
    expect(since.toISOString()).toBe("2026-04-14T07:00:00.000Z");
    expect(until.toISOString()).toBe("2026-04-15T07:00:00.000Z");
  });
});

describe("todayInTimezone", () => {
  it("returns yyyy-MM-dd string", () => {
    expect(todayInTimezone("UTC")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
