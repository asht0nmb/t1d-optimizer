"use client";

import { useEffect, useState } from "react";
import { subDays, format } from "date-fns";
import { HeatmapGrid } from "@/components/HeatmapGrid";
import type { BgTargets, ConfigResponse, HeatmapResponse } from "@/lib/types/api";

export default function HeatmapPage() {
  const [data, setData] = useState<HeatmapResponse | null>(null);
  const [targets, setTargets] = useState<BgTargets | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json() as Promise<ConfigResponse>)
      .then((config) => {
        setTargets(config.bg_targets);
        const to = config.date_bounds?.max_date ?? format(new Date(), "yyyy-MM-dd");
        const fromCandidate = format(subDays(new Date(to), 30), "yyyy-MM-dd");
        const from = config.date_bounds
          ? fromCandidate < config.date_bounds.min_date
            ? config.date_bounds.min_date
            : fromCandidate
          : fromCandidate;
        return fetch(`/api/heatmap?from=${from}&to=${to}`).then((r) => r.json());
      })
      .then((heatmap) => {
        if (heatmap.error) setError(heatmap.error);
        else setData(heatmap);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load heatmap");
      });
  }, []);

  if (error) return <p className="text-red-600">{error}</p>;
  if (!data || !targets) return <p className="text-slate-500">Loading…</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">BG heatmap</h1>
      <p className="text-sm text-slate-600">
        Median BG by hour-of-day (last 30 days). Green = in range, orange/red =
        high/low.
      </p>
      <HeatmapGrid data={data} low={targets.low} high={targets.high} />
    </div>
  );
}
