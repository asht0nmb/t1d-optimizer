import { describe, expect, it } from "vitest";
import {
  alertMessage,
  clampPage,
  clampPageSize,
  shapeAlertRows,
} from "@/lib/alerts";

describe("alertMessage", () => {
  it("extracts payload.message when it is a non-empty string", () => {
    expect(alertMessage({ message: "Possible missed meal" })).toBe(
      "Possible missed meal",
    );
  });

  it("returns null for missing or non-string message", () => {
    expect(alertMessage({})).toBeNull();
    expect(alertMessage(null)).toBeNull();
    expect(alertMessage("just a string")).toBeNull();
    expect(alertMessage({ message: 5 })).toBeNull();
    expect(alertMessage({ message: "" })).toBeNull();
  });
});

describe("shapeAlertRows", () => {
  it("shapes a row and trims fired_at to minutes", () => {
    const out = shapeAlertRows([
      {
        id: "12",
        alert_kind: "missed_meal",
        fired_at_local: "2026-06-10 20:15:42.123456",
        pump_serial: null,
        event_ref: "meal-2026-06-10T20:00",
        delivery: "sent",
        payload: { message: "Possible missed meal bolus" },
        total: "57",
      },
    ]);
    expect(out).toEqual([
      {
        id: 12,
        alert_kind: "missed_meal",
        fired_at: "2026-06-10 20:15",
        pump_serial: null,
        event_ref: "meal-2026-06-10T20:00",
        delivery: "sent",
        message: "Possible missed meal bolus",
      },
    ]);
  });

  it("handles payloads without a message", () => {
    const out = shapeAlertRows([
      {
        id: 3,
        alert_kind: "anomaly_spike",
        fired_at_local: "2026-06-09 07:00:00",
        pump_serial: "SN123",
        event_ref: null,
        delivery: "pending",
        payload: { spike_mgdl: 240 },
        total: 1,
      },
    ]);
    expect(out[0].message).toBeNull();
    expect(out[0].pump_serial).toBe("SN123");
    expect(out[0].fired_at).toBe("2026-06-09 07:00");
  });
});

describe("pagination clamps", () => {
  it("clampPage floors and lower-bounds to 1", () => {
    expect(clampPage(1)).toBe(1);
    expect(clampPage(0)).toBe(1);
    expect(clampPage(-3)).toBe(1);
    expect(clampPage(2.7)).toBe(2);
    expect(clampPage(Number.NaN)).toBe(1);
  });

  it("clampPageSize bounds to [1, 100] with fallback 30", () => {
    expect(clampPageSize(30)).toBe(30);
    expect(clampPageSize(1)).toBe(1);
    expect(clampPageSize(0)).toBe(30);
    expect(clampPageSize(-10)).toBe(30);
    expect(clampPageSize(500)).toBe(100);
    expect(clampPageSize(Number.NaN)).toBe(30);
  });
});
