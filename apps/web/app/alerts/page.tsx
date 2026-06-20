"use client";

import { useCallback, useEffect, useState } from "react";
import type { AlertsResponse } from "@/lib/types/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { deliveryBadge } from "@/lib/badge-variant";

export default function AlertsPage() {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetch(`/api/alerts?page=${page}&page_size=30`).then((r) =>
        r.json(),
      );
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = data
    ? Math.max(1, Math.ceil(data.total / data.page_size))
    : 1;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Alerts history</h1>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="space-y-2 p-4">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-40 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && data.alerts.length === 0 ? (
          <EmptyState
            title="No alerts recorded yet"
            description="Meal-rise alerts will appear here once they fire."
          />
        ) : data ? (
          <Card className="p-4">
            <p className="text-sm text-muted-foreground">
              {data.total} alerts (page {data.page} of {totalPages})
            </p>
            <table className="mt-3 w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2">Fired at</th>
                  <th>Kind</th>
                  <th>Delivery</th>
                  <th>Message</th>
                  <th>Pump</th>
                </tr>
              </thead>
              <tbody>
                {data.alerts.map((a) => {
                  const b = deliveryBadge(a.delivery);
                  return (
                    <tr
                      key={a.id}
                      className="border-b border-border last:border-0"
                    >
                      <td className="whitespace-nowrap py-2">{a.fired_at}</td>
                      <td>{a.alert_kind}</td>
                      <td>
                        <Badge variant={b.variant}>{b.label}</Badge>
                      </td>
                      <td className="text-muted-foreground">{a.message ?? "—"}</td>
                      <td>{a.pump_serial ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="mt-3 flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage(page + 1)}
              >
                Next
              </Button>
            </div>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
