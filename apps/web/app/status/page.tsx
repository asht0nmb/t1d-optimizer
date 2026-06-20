"use client";

import { useCallback, useEffect, useState } from "react";
import type { StatusResponse } from "@/lib/types/api";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { freshnessBadge } from "@/lib/badge-variant";

export default function StatusPage() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetch("/api/status").then((r) => r.json());
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Automation status</h1>

      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && data.signals.length === 0 ? (
          <EmptyState
            title="No automation signals"
            description="Nothing has reported a sync or run yet."
          />
        ) : data ? (
          <>
            <p className="text-sm text-muted-foreground">
              Times shown in {data.timezone}
            </p>
            <div className="mt-3 overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="p-3 font-medium">Signal</th>
                    <th className="p-3 font-medium">Last seen</th>
                    <th className="p-3 font-medium">Status</th>
                    <th className="p-3 font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {data.signals.map((s) => {
                    const b = freshnessBadge(s.freshness);
                    return (
                      <tr
                        key={s.label}
                        className="border-b border-border last:border-0"
                      >
                        <td className="whitespace-nowrap p-3 font-medium">
                          {s.label}
                        </td>
                        <td className="whitespace-nowrap p-3 text-muted-foreground">
                          {s.timestamp ?? "—"}
                        </td>
                        <td className="p-3">
                          <Badge variant={b.variant}>{b.label}</Badge>
                        </td>
                        <td className="p-3 text-muted-foreground">
                          {s.detail ?? "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
