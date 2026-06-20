import { describe, expect, it } from "vitest";
import {
  bandColors,
  buildBandSegments,
  formatMetric,
  sufficiencyNote,
} from "@/lib/report";
import type { CgmReportResponse } from "@/lib/types/api";

function makeReport(overrides: Partial<CgmReportResponse> = {}): CgmReportResponse {
  return {
    end_date: "2025-06-01",
    days: 14,
    tz: "America/Los_Angeles",
    n_readings: 4000,
    expected_readings: 4032,
    active_pct: 99.2,
    days_covered: 14,
    meets_sufficiency: true,
    tbr2: 1,
    tbr1: 4,
    tir: 70,
    tar1: 20,
    tar2: 5,
    tbr_total: 5,
    tar_total: 25,
    titr: 50,
    tir_config: 70,
    mean_bg: 150,
    median_bg: 145,
    sd_bg: 45,
    cv_pct: 30,
    cv_stable: true,
    gmi: 6.8,
    ea1c: 6.9,
    lbgi: 1.2,
    hbgi: 4.5,
    gri: 42,
    gri_hypo: 5,
    gri_hyper: 37,
    j_index: 30,
    modd: 40,
    conga: 35,
    mage: 90,
    ...overrides,
  };
}

describe("buildBandSegments", () => {
  it("returns the five bands in low→high order with palette colors", () => {
    const segs = buildBandSegments(makeReport());
    expect(segs.map((s) => s.key)).toEqual([
      "tbr2",
      "tbr1",
      "tir",
      "tar1",
      "tar2",
    ]);
    expect(segs[2]).toMatchObject({ label: "In range", pct: 70, color: bandColors.tir });
  });

  it("clamps negative or non-finite band values to 0", () => {
    const segs = buildBandSegments(
      makeReport({ tbr2: -3, tar2: Number.NaN as unknown as number }),
    );
    expect(segs[0].pct).toBe(0);
    expect(segs[4].pct).toBe(0);
  });
});

describe("formatMetric", () => {
  it("renders em-dash for null/undefined/NaN", () => {
    expect(formatMetric(null)).toBe("—");
    expect(formatMetric(undefined)).toBe("—");
    expect(formatMetric(Number.NaN)).toBe("—");
  });

  it("applies digits and suffix", () => {
    expect(formatMetric(6.83, { suffix: "%" })).toBe("6.8%");
    expect(formatMetric(150.4, { digits: 0, suffix: " mg/dL" })).toBe("150 mg/dL");
    expect(formatMetric(42, { digits: 0 })).toBe("42");
  });
});

describe("sufficiencyNote", () => {
  it("returns null when sufficiency is met", () => {
    expect(sufficiencyNote(makeReport({ meets_sufficiency: true }))).toBeNull();
  });

  it("describes the gate when not met", () => {
    const note = sufficiencyNote(
      makeReport({ meets_sufficiency: false, days_covered: 8, active_pct: 55 }),
    );
    expect(note).toContain("8 days");
    expect(note).toContain("55% active");
    expect(note).toContain("withheld");
  });
});
