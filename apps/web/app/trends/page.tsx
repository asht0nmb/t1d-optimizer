"use client";

import { useEffect, useState } from "react";
import { TrendsChart } from "@/components/TrendsChart";
import type { TrendsResponse } from "@/lib/types/api";

export default function TrendsPage() {
  const [days, setDays] = useState<7 | 14 | 30>(14);
  const [data, setData] = useState<TrendsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/trends?days=${days}`)
      .then((r) => r.json())
      .then((body) => {
        if (body.error) setError(body.error);
        else setData(body);
      });
  }, [days]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">TIR trends</h1>
      <div className="flex gap-2">
        {([7, 14, 30] as const).map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => setDays(d)}
            className={`rounded px-3 py-1 text-sm ${
              days === d ? "bg-slate-900 text-white" : "bg-white border border-slate-200"
            }`}
          >
            {d}d
          </button>
        ))}
      </div>
      {error && <p className="text-red-600">{error}</p>}
      {!data && !error && <p className="text-slate-500">Loading…</p>}
      {data && <TrendsChart data={data} />}
    </div>
  );
}
