"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  CalendarDays,
  GitCompare,
  Grid3x3,
  LineChart,
  Search,
} from "lucide-react";
import type {
  AlertsResponse,
  StatusResponse,
  TrendsResponse,
} from "@/lib/types/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { deliveryBadge, freshnessBadge } from "@/lib/badge-variant";

interface OverviewData {
  trends: TrendsResponse | null;
  status: StatusResponse | null;
  alerts: AlertsResponse | null;
}

const quickLinks = [
  { href: "/day", label: "Day view", icon: CalendarDays },
  { href: "/heatmap", label: "Heatmap", icon: Grid3x3 },
  { href: "/agp", label: "AGP", icon: LineChart },
  { href: "/compare", label: "Compare", icon: GitCompare },
  { href: "/search", label: "Search", icon: Search },
];

export default function HomePage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [trends, status, alerts] = await Promise.all([
        fetch("/api/trends?days=7").then((r) => r.json()),
        fetch("/api/status").then((r) => r.json()),
        fetch("/api/alerts?page=1&page_size=5").then((r) => r.json()),
      ]);
      const firstError = trends.error ?? status.error ?? alerts.error;
      if (firstError) {
        setError(firstError);
      } else {
        setData({ trends, status, alerts });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const latest = data?.trends?.points.at(-1) ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Overview</h1>
        <p className="text-sm text-muted-foreground">
          At-a-glance status of your glucose data and automation.
        </p>
      </div>

      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : null}

      <div
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
        aria-busy={loading}
      >
        {/* Latest TIR */}
        <Card>
          <CardHeader>
            <CardTitle>Latest time in range</CardTitle>
            <CardDescription>
              {latest ? latest.date : "Most recent day"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className="h-9 w-24" />
            ) : latest ? (
              <div className="space-y-2">
                <p className="text-3xl font-semibold tabular-nums">
                  {latest.tir_pct.toFixed(0)}
                  <span className="ml-1 text-base font-normal text-muted-foreground">
                    %
                  </span>
                </p>
                <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <span>Below {latest.below_pct.toFixed(0)}%</span>
                  <span>Above {latest.above_pct.toFixed(0)}%</span>
                  <span>{latest.reading_count} readings</span>
                </div>
                <Link
                  href="/trends"
                  className="inline-flex items-center gap-1 text-sm text-primary hover:underline focus-visible:underline focus-visible:outline-none"
                >
                  View TIR trends <ArrowRight className="size-3" aria-hidden="true" />
                </Link>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No recent data.</p>
            )}
          </CardContent>
        </Card>

        {/* Data freshness */}
        <Card>
          <CardHeader>
            <CardTitle>Data freshness</CardTitle>
            <CardDescription>Automation signals</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-2">
                <Skeleton className="h-5 w-full" />
                <Skeleton className="h-5 w-full" />
                <Skeleton className="h-5 w-2/3" />
              </div>
            ) : data?.status && data.status.signals.length > 0 ? (
              <ul className="space-y-2">
                {data.status.signals.slice(0, 4).map((s) => {
                  const b = freshnessBadge(s.freshness);
                  return (
                    <li
                      key={s.label}
                      className="flex items-center justify-between gap-2 text-sm"
                    >
                      <span className="text-muted-foreground">{s.label}</span>
                      <Badge variant={b.variant}>{b.label}</Badge>
                    </li>
                  );
                })}
                <li>
                  <Link
                    href="/status"
                    className="inline-flex items-center gap-1 text-sm text-primary hover:underline focus-visible:underline focus-visible:outline-none"
                  >
                    Full status <ArrowRight className="size-3" aria-hidden="true" />
                  </Link>
                </li>
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No signals.</p>
            )}
          </CardContent>
        </Card>

        {/* Recent alerts */}
        <Card>
          <CardHeader>
            <CardTitle>Recent alerts</CardTitle>
            <CardDescription>Last 5 fired</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-2">
                <Skeleton className="h-5 w-full" />
                <Skeleton className="h-5 w-full" />
                <Skeleton className="h-5 w-3/4" />
              </div>
            ) : data?.alerts && data.alerts.alerts.length > 0 ? (
              <ul className="space-y-2">
                {data.alerts.alerts.slice(0, 4).map((a) => {
                  const b = deliveryBadge(a.delivery);
                  return (
                    <li
                      key={a.id}
                      className="flex items-center justify-between gap-2 text-sm"
                    >
                      <span className="truncate text-muted-foreground">
                        {a.fired_at}
                      </span>
                      <Badge variant={b.variant}>{b.label}</Badge>
                    </li>
                  );
                })}
                <li>
                  <Link
                    href="/alerts"
                    className="inline-flex items-center gap-1 text-sm text-primary hover:underline focus-visible:underline focus-visible:outline-none"
                  >
                    All alerts <ArrowRight className="size-3" aria-hidden="true" />
                  </Link>
                </li>
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                No alerts recorded yet.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Quick links */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Jump to
        </h2>
        <div className="flex flex-wrap gap-2">
          {quickLinks.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
