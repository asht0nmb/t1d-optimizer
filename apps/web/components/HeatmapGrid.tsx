"use client";

import type { HeatmapResponse } from "@/lib/types/api";

function bgColor(value: number | null, low: number, high: number): string {
  if (value == null) return "#f1f5f9";
  if (value < low) return "#fecaca";
  if (value > 250) return "#fca5a5";
  if (value > high) return "#fed7aa";
  return "#bbf7d0";
}

export function HeatmapGrid({
  data,
  low,
  high,
}: {
  data: HeatmapResponse;
  low: number;
  high: number;
}) {
  const hours = Array.from({ length: 24 }, (_, i) => i);
  const dates = [...new Set(data.cells.map((c) => c.date))].sort();
  const byKey = new Map(
    data.cells.map((c) => [`${c.date}-${c.hour}`, c.median_bg ?? c.avg_bg]),
  );

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-xs">
        <thead>
          <tr>
            <th className="sticky left-0 bg-white p-1 text-left">Date</th>
            {hours.map((h) => (
              <th key={h} className="p-1 font-normal text-slate-500">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dates.map((d) => (
            <tr key={d}>
              <td className="sticky left-0 bg-white p-1 font-medium text-slate-700">
                {d}
              </td>
              {hours.map((h) => {
                const v = byKey.get(`${d}-${h}`) ?? null;
                return (
                  <td
                    key={h}
                    className="h-6 w-6 border border-white p-0"
                    style={{ backgroundColor: bgColor(v, low, high) }}
                    title={v != null ? `${d} ${h}:00 — ${Math.round(v)} mg/dL` : ""}
                  />
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
