"use client";

import { useEffect, useState } from "react";
import { subDays, format } from "date-fns";
import { HeatmapGrid } from "@/components/HeatmapGrid";
import type { BgTargets, HeatmapResponse } from "@/lib/types/api";

export default function HeatmapPage() {
  const [data, setData] = useState<HeatmapResponse | null>(null);
  const [targets, setTargets] = useState<BgTargets | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const to = format(new Date(), "yyyy-MM-dd");
    const from = format(subDays(new Date(), 30), "yyyy-MM-dd");
    Promise.all([
      fetch(`/api/heatmap?from=${from}&to=${to}`).then((r) => r.json()),
      fetch("/api/config").then((r) => r.json()),
    ]).then(([heatmap, config]) => {
      if (heatmap.error) setError(heatmap.error);
      else setData(heatmap);
      setTargets(config.bg_targets);
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
