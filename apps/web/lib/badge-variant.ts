/**
 * Pure mappers from domain status strings to Badge variants + accessible
 * labels. Keeping this side-effect-free makes it unit-testable and keeps the
 * color/label policy in one place (no color-only signalling).
 */
import type { BadgeVariant } from "@/components/ui/badge";

export interface BadgeDescriptor {
  variant: BadgeVariant;
  label: string;
}

/** fetch_state freshness → badge. */
export function freshnessBadge(
  freshness: "ok" | "stale" | "missing" | string,
): BadgeDescriptor {
  switch (freshness) {
    case "ok":
      return { variant: "success", label: "OK" };
    case "stale":
      return { variant: "warning", label: "Stale" };
    case "missing":
      return { variant: "destructive", label: "Missing" };
    default:
      return { variant: "default", label: freshness || "Unknown" };
  }
}

/** alerts_sent delivery → badge. */
export function deliveryBadge(
  delivery: "sent" | "pending" | "failed" | string,
): BadgeDescriptor {
  switch (delivery) {
    case "sent":
      return { variant: "success", label: "Sent" };
    case "failed":
      return { variant: "destructive", label: "Failed" };
    case "pending":
      return { variant: "warning", label: "Pending" };
    default:
      return { variant: "default", label: delivery || "Unknown" };
  }
}
