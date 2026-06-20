import { describe, expect, it } from "vitest";
import { freshnessBadge, deliveryBadge } from "@/lib/badge-variant";

describe("freshnessBadge", () => {
  it("maps ok to success with a text label", () => {
    expect(freshnessBadge("ok")).toEqual({ variant: "success", label: "OK" });
  });

  it("maps stale to warning", () => {
    expect(freshnessBadge("stale")).toEqual({
      variant: "warning",
      label: "Stale",
    });
  });

  it("maps missing to destructive", () => {
    expect(freshnessBadge("missing")).toEqual({
      variant: "destructive",
      label: "Missing",
    });
  });

  it("falls back to default for unknown values", () => {
    expect(freshnessBadge("weird")).toEqual({
      variant: "default",
      label: "weird",
    });
  });
});

describe("deliveryBadge", () => {
  it("maps sent to success", () => {
    expect(deliveryBadge("sent")).toEqual({ variant: "success", label: "Sent" });
  });

  it("maps failed to destructive", () => {
    expect(deliveryBadge("failed")).toEqual({
      variant: "destructive",
      label: "Failed",
    });
  });

  it("maps pending to warning", () => {
    expect(deliveryBadge("pending")).toEqual({
      variant: "warning",
      label: "Pending",
    });
  });

  it("falls back to default for unknown values", () => {
    expect(deliveryBadge("")).toEqual({ variant: "default", label: "Unknown" });
  });
});
