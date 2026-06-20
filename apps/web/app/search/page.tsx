"use client";

import Link from "next/link";
import { useState } from "react";
import type { SearchResponse } from "@/lib/types/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function SearchPage() {
  const [tirBelow, setTirBelow] = useState("50");
  const [alarmsAbove, setAlarmsAbove] = useState("");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run(page = 1) {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: "30" });
      if (tirBelow) params.set("tir_below", tirBelow);
      if (alarmsAbove) params.set("alarms_above", alarmsAbove);
      const res = await fetch(`/api/search?${params}`);
      const body = await res.json();
      if (!res.ok) setError(body.error ?? "Search failed");
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Search days</h1>
      <Card>
        <form
          className="flex flex-wrap items-end gap-4 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(1);
          }}
        >
          <label className="text-sm">
            TIR below (%)
            <input
              type="number"
              value={tirBelow}
              onChange={(e) => setTirBelow(e.target.value)}
              className="mt-1 block w-24 rounded border border-border px-2 py-1"
            />
          </label>
          <label className="text-sm">
            Alarms above
            <input
              type="number"
              value={alarmsAbove}
              onChange={(e) => setAlarmsAbove(e.target.value)}
              className="mt-1 block w-24 rounded border border-border px-2 py-1"
              placeholder="optional"
            />
          </label>
          <Button type="submit" disabled={loading}>
            Search
          </Button>
        </form>
      </Card>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="space-y-2 p-4">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-32 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void run(1)} />
        ) : data && data.results.length === 0 ? (
          <EmptyState
            title="No matching days"
            description="Try loosening the TIR or alarm thresholds."
          />
        ) : data ? (
          <Card className="p-4">
            <p className="text-sm text-muted-foreground">
              {data.total} matching days (page {data.page})
            </p>
            <table className="mt-3 w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2">Date</th>
                  <th>TIR %</th>
                  <th>Alarms</th>
                  <th>Lows</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((r) => (
                  <tr key={r.date} className="border-b border-border last:border-0">
                    <td className="py-2">
                      <Link
                        href={`/day/${r.date}`}
                        className="text-primary hover:underline"
                      >
                        {r.date}
                      </Link>
                    </td>
                    <td>{r.tir_pct.toFixed(0)}</td>
                    <td>{r.alarm_count}</td>
                    <td>{r.low_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
