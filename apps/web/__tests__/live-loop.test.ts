import { describe, expect, it } from "vitest";
import { buildLiveSignals, type LiveCronRow } from "@/lib/live-loop";

const NOW = "2026-10-14T20:00:00.000Z"; // 13:00 America/Los_Angeles
const TZ = "America/Los_Angeles";

const iso = (minAgo: number) =>
  new Date(new Date(NOW).getTime() - minAgo * 60_000).toISOString();

function row(syncedMinAgo: number | null, meta: unknown): LiveCronRow {
  const utc = syncedMinAgo === null ? null : iso(syncedMinAgo);
  return { last_synced_at: utc, last_synced_at_utc: utc, meta };
}

function bySignal(r: LiveCronRow | null) {
  const [worker, cgm] = buildLiveSignals(r, TZ, NOW);
  return { worker, cgm };
}

describe("buildLiveSignals", () => {
  it("emits worker + CGM signals with new labels", () => {
    const { worker, cgm } = bySignal(row(1, {}));
    expect(worker.label).toBe("Live loop (worker)");
    expect(cgm.label).toBe("Live CGM");
  });

  it("no row at all -> both missing", () => {
    const { worker, cgm } = bySignal(null);
    expect(worker.freshness).toBe("missing");
    expect(cgm.freshness).toBe("missing");
    expect(cgm.detail).toBeNull();
  });

  it.each([
    ["legacy {last_cursor: null}", { last_cursor: null }],
    ["null", null],
    ["string", "oops"],
    ["array", [1, 2]],
    ["garbage fields", { last_cycle_at: 42, last_outcome: {}, outcome_counts: "x", last_reading_age_min: "n/a" }],
    ["unparseable last_cycle_at", { last_cycle_at: "not-a-date" }],
  ])("missing/malformed meta (%s) falls back to last_synced_at", (_n, meta) => {
    const fresh = bySignal(row(3, meta));
    expect(fresh.worker.freshness).toBe("ok");
    expect(fresh.worker.detail).toContain("legacy");
    expect(fresh.cgm.freshness).toBe("ok");
    expect(fresh.cgm.detail).toBeNull();

    const stale = bySignal(row(40, meta));
    expect(stale.worker.freshness).toBe("stale");
    expect(stale.cgm.freshness).toBe("stale");
  });

  it("stale worker: last_cycle_at > 15 min -> worker stale", () => {
    const { worker } = bySignal(
      row(60, { last_cycle_at: iso(20), last_outcome: "error" }),
    );
    expect(worker.freshness).toBe("stale");
  });

  it("fresh worker with stale CGM: worker ok, CGM stale, detail explains", () => {
    const { worker, cgm } = bySignal(
      row(26 * 60, {
        last_cycle_at: iso(2),
        last_outcome: "no_data",
        last_reading_age_min: 1500.4,
      }),
    );
    expect(worker.freshness).toBe("ok");
    expect(cgm.freshness).toBe("stale");
    expect(cgm.detail).toContain("last: no_data");
    expect(cgm.detail).toContain("reading 1500 min old");
  });

  it("all healthy, with newest day's outcome counts", () => {
    const { worker, cgm } = bySignal(
      row(4, {
        last_cycle_at: iso(1),
        last_outcome: "no_candidate",
        last_reading_age_min: 3,
        outcome_counts: {
          "2026-10-13": { stale: 99 },
          "2026-10-14": { no_candidate: 140, stale: 3, error: 0 },
        },
      }),
    );
    expect(worker.freshness).toBe("ok");
    expect(worker.timestamp).toBe("2026-10-14 12:59");
    expect(cgm.freshness).toBe("ok");
    expect(cgm.detail).toBe(
      "last: no_candidate · reading 3 min old · counts 2026-10-14: no_candidate 140 · stale 3",
    );
  });

  it("worker prefers meta.last_cycle_at over last_synced_at", () => {
    const { worker, cgm } = bySignal(
      row(120, { last_cycle_at: iso(5) }),
    );
    expect(worker.freshness).toBe("ok");
    expect(cgm.freshness).toBe("stale");
  });

  it("15-minute boundary is inclusive", () => {
    expect(bySignal(row(15, {})).cgm.freshness).toBe("ok");
    expect(bySignal(row(16, {})).cgm.freshness).toBe("stale");
  });

  it("shows the worker's newest day key as-is, even if it differs from web 'today'", () => {
    // Web tz (LA) says 2026-10-14; the worker keyed by a different tz.
    const { cgm } = bySignal(
      row(4, {
        outcome_counts: {
          "2026-10-13": { stale: 9 },
          "2026-10-15": { no_candidate: 2 },
          "not-a-day": { error: 1 },
        },
      }),
    );
    expect(cgm.detail).toBe("counts 2026-10-15: no_candidate 2");
  });
});
