"use client";
import { fetchJson } from "@/lib/fetch-json";

import { useCallback, useEffect, useState } from "react";
import { CompareChart } from "@/components/CompareChart";
import type { CompareResponse, ConfigResponse } from "@/lib/types/api";
import { defaultCompareDate } from "@/lib/dates";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function ComparePage() {
  const [dateA, setDateA] = useState("2026-04-14");
  const [dateB, setDateB] = useState(defaultCompareDate("2026-04-14"));
  const [minDate, setMinDate] = useState<string | undefined>(undefined);
  const [maxDate, setMaxDate] = useState<string | undefined>(undefined);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchJson<CompareResponse>(`/api/compare?a=${dateA}&b=${dateB}`);
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load comparison");
    } finally {
      setLoading(false);
    }
  }, [dateA, dateB]);

  useEffect(() => {
    void load();
  }, [load]);

  const isEmpty =
    data != null &&
    data.series_a.length === 0 &&
    data.series_b.length === 0;

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
            className="mt-1 block rounded border border-border px-2 py-1"
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
            className="mt-1 block rounded border border-border px-2 py-1"
          />
        </label>
      </div>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="p-4">
            <Skeleton className="h-80 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : isEmpty ? (
          <EmptyState
            title="No CGM data on either day"
            description="Pick two days that both have readings to overlay them."
          />
        ) : data ? (
          <Card className="p-4">
            <CompareChart data={data} />
          </Card>
        ) : null}
      </div>
    </div>
  );
}
