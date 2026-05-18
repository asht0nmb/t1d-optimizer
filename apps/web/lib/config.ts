import { readFileSync } from "fs";
import { join } from "path";
import type { BgTargets } from "@/lib/types/api";

import defaults from "@/config/bg-targets.json";

export function getTimezone(): string {
  return process.env.TZ ?? "America/Los_Angeles";
}

export function getPumpSerial(): string | undefined {
  return process.env.DEFAULT_PUMP_SERIAL || undefined;
}

/** BG band targets — never hardcode in UI; always fetch via /api/config or embedded in API payloads. */
export function loadBgTargets(): BgTargets {
  const path = process.env.USER_CONFIG_PATH;
  if (path) {
    try {
      const raw = readFileSync(path, "utf8");
      const parsed = JSON.parse(raw) as { bg_targets?: BgTargets };
      if (parsed.bg_targets) return parsed.bg_targets;
    } catch {
      // fall through
    }
  }
  return {
    low: defaults.low,
    high: defaults.high,
    target: defaults.target,
  };
}

export function configPath(): string {
  return (
    process.env.USER_CONFIG_PATH ??
    join(process.cwd(), "config", "bg-targets.json")
  );
}
