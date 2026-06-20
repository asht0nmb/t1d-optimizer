import { describe, expect, it } from "vitest";
import { dayRangeUtc } from "@/lib/dates";

describe("dayRangeUtc", () => {
  it("returns UTC instant bounds for an inclusive local-day range", () => {
    // America/Los_Angeles is UTC-8 in winter (PST).
    const { since, until } = dayRangeUtc(
      "2026-03-01",
      "2026-03-07",
      "America/Los_Angeles",
    );
    expect(since.toISOString()).toBe("2026-03-01T08:00:00.000Z");
    // Half-open: end is start of the day AFTER 2026-03-07.
    expect(until.toISOString()).toBe("2026-03-08T08:00:00.000Z");
  });

  it("handles a DST-transition start date (spring forward) correctly", () => {
    // DST begins 2026-03-08 02:00 local; the local-midnight instant on
    // 2026-03-08 is still PST (UTC-8), so start = 08:00Z.
    const { since } = dayRangeUtc(
      "2026-03-08",
      "2026-03-08",
      "America/Los_Angeles",
    );
    expect(since.toISOString()).toBe("2026-03-08T08:00:00.000Z");
  });

  it("yields a post-DST offset for the day after spring-forward", () => {
    // 2026-03-09 is fully PDT (UTC-7), so local midnight = 07:00Z, and the
    // half-open end (start of 2026-03-10) is also PDT = 07:00Z.
    const { since, until } = dayRangeUtc(
      "2026-03-09",
      "2026-03-09",
      "America/Los_Angeles",
    );
    expect(since.toISOString()).toBe("2026-03-09T07:00:00.000Z");
    expect(until.toISOString()).toBe("2026-03-10T07:00:00.000Z");
  });

  it("spans across a DST boundary using each day's own offset", () => {
    // Range Mar 7 (PST) through Mar 9 (PDT): start is PST midnight (08:00Z),
    // end is start of Mar 10 in PDT (07:00Z).
    const { since, until } = dayRangeUtc(
      "2026-03-07",
      "2026-03-09",
      "America/Los_Angeles",
    );
    expect(since.toISOString()).toBe("2026-03-07T08:00:00.000Z");
    expect(until.toISOString()).toBe("2026-03-10T07:00:00.000Z");
  });
});
