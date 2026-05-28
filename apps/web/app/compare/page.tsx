"use client";

import { useEffect, useState } from "react";
import { CompareChart } from "@/components/CompareChart";
import type { CompareResponse, ConfigResponse } from "@/lib/types/api";
import { defaultCompareDate } from "@/lib/dates";

export default function ComparePage() {
  const [dateA, setDateA] = useState("2026-04-14");
  const [dateB, setDateB] = useState(defaultCompareDate("2026-04-14"));
  const [minDate, setMinDate] = useState<string | undefined>(undefined);
  const [maxDate, setMaxDate] = useState<string | undefined>(undefined);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json() as Promise<ConfigResponse>)
      .then((config) => {
        const bounds = config.date_bounds;
        if (!bounds) return;
        setMinDate(bounds.min_date);
        setMaxDate(bounds.max_date);
        setDateA(bounds.max_date);
        const prior = defaultCompareDate(bounds.max_date);
        setDateB(prior < bounds.min_date ? bounds.min_date : prior);
      })
      .catch(() => {
        // Keep hardcoded fallback when config endpoint is unavailable.
      });
  }, []);

  useEffect(() => {
    fetch(`/api/compare?a=${dateA}&b=${dateB}`)
      .then((r) => r.json())
      .then((body) => {
        if (body.error) setError(body.error);
        else setData(body);
      });
  }, [dateA, dateB]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Compare days</h1>
      <div className="flex flex-wrap gap-4">
        <label className="text-sm">
          Day A
          <input
            type="date"
            value={dateA}
            onChange={(e) => setDateA(e.target.value)}
            min={minDate}
            max={maxDate}
            className="mt-1 block rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="text-sm">
          Day B
          <input
            type="date"
            value={dateB}
            onChange={(e) => setDateB(e.target.value)}
            min={minDate}
            max={maxDate}
            className="mt-1 block rounded border border-slate-300 px-2 py-1"
          />
        </label>
      </div>
      {error && <p className="text-red-600">{error}</p>}
      {!data && !error && <p className="text-slate-500">Loading…</p>}
      {data && <CompareChart data={data} />}
    </div>
  );
}
