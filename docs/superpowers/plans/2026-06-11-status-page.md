# Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automation-health `/status` page to the Next.js app that surfaces CGM data recency, Tandem sync freshness, meal-rise detection recency, and last alert delivery at a glance.

**Architecture:** Four new files plus edits to two existing files. A pure helper (`lib/status.ts`) classifies timestamps into ok/stale/missing — vitest-testable with no pg imports. The DB query layer (`lib/queries/status.ts`) reads `cgm`, `fetch_state`, `detection_results`, and `alerts_sent` using the existing `queryRows` pool. The API route (`app/api/status/route.ts`) is session-guarded like every other data route. The page (`app/status/page.tsx`) is a client component matching the alerts page pattern — fetch on mount, badge-per-row layout.

**Tech Stack:** Next.js 14 App Router, TypeScript, Tailwind CSS, Vitest, pg pool (`queryRows` from `lib/queries/db.ts`).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `lib/status.ts` | Pure freshness classifier — no pg imports |
| Create | `__tests__/status.test.ts` | Vitest tests for the classifier |
| Create | `lib/queries/status.ts` | DB query: latest cgm timestamp, all fetch_state rows, latest detection_results row, latest alerts_sent row |
| Modify | `lib/types/api.ts` | Add `FetchStateRow`, `StatusSignal`, `StatusResponse` |
| Create | `app/api/status/route.ts` | GET handler — session-guarded, calls fetchStatus |
| Create | `app/status/page.tsx` | Client page — fetch /api/status, render card table with freshness badges |
| Modify | `components/AppNav.tsx` | Add `{ href: "/status", label: "Status" }` to links array |

---

### Task 1: Pure freshness classifier + tests

**Files:**
- Create: `apps/web/lib/status.ts`
- Create: `apps/web/__tests__/status.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// apps/web/__tests__/status.test.ts
import { describe, expect, it } from "vitest";
import { classifyFreshness } from "@/lib/status";

describe("classifyFreshness", () => {
  const now = "2026-06-11T12:00:00.000Z";

  it("returns 'missing' when timestamp is null", () => {
    expect(classifyFreshness(null, 26, now)).toBe("missing");
  });

  it("returns 'missing' when timestamp is undefined", () => {
    expect(classifyFreshness(undefined, 26, now)).toBe("missing");
  });

  it("returns 'ok' when timestamp is within threshold", () => {
    // 25 hours ago — under 26h threshold
    const ts = "2026-06-10T11:00:00.000Z";
    expect(classifyFreshness(ts, 26, now)).toBe("ok");
  });

  it("returns 'stale' when timestamp is beyond threshold", () => {
    // 27 hours ago — over 26h threshold
    const ts = "2026-06-10T09:00:00.000Z";
    expect(classifyFreshness(ts, 26, now)).toBe("stale");
  });

  it("returns 'ok' when timestamp equals threshold exactly", () => {
    // exactly 26 hours ago
    const ts = "2026-06-10T10:00:00.000Z";
    expect(classifyFreshness(ts, 26, now)).toBe("ok");
  });

  it("uses Date.now() when referenceNow is omitted", () => {
    // A very recent timestamp should always be ok
    const ts = new Date(Date.now() - 1000).toISOString();
    expect(classifyFreshness(ts, 26)).toBe("ok");
  });

  it("uses different thresholds correctly (24h detection threshold)", () => {
    // 23 hours ago — under 24h
    const ts = "2026-06-10T13:00:00.000Z";
    expect(classifyFreshness(ts, 24, now)).toBe("ok");
    // 25 hours ago — over 24h
    const ts2 = "2026-06-10T11:00:00.000Z";
    expect(classifyFreshness(ts2, 24, now)).toBe("stale");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx vitest run __tests__/status.test.ts
```

Expected: FAIL — "Cannot find module '@/lib/status'"

- [ ] **Step 3: Implement the classifier**

```typescript
// apps/web/lib/status.ts
/**
 * Pure freshness classifier — no pg imports, safe to import in vitest.
 *
 * Given an ISO timestamp and a threshold in hours, returns:
 *   "ok"      — timestamp is within threshold hours of referenceNow
 *   "stale"   — timestamp exists but is older than threshold hours
 *   "missing" — timestamp is null or undefined
 */
export type FreshnessStatus = "ok" | "stale" | "missing";

export function classifyFreshness(
  timestamp: string | null | undefined,
  thresholdHours: number,
  referenceNow?: string,
): FreshnessStatus {
  if (timestamp == null) return "missing";
  const ref = referenceNow ? new Date(referenceNow).getTime() : Date.now();
  const ts = new Date(timestamp).getTime();
  const ageHours = (ref - ts) / (1000 * 60 * 60);
  return ageHours <= thresholdHours ? "ok" : "stale";
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx vitest run __tests__/status.test.ts
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine && git add apps/web/lib/status.ts apps/web/__tests__/status.test.ts && git commit -m "$(cat <<'EOF'
feat(web): add pure freshness classifier with tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: API types

**Files:**
- Modify: `apps/web/lib/types/api.ts`

- [ ] **Step 1: Append the new types to the bottom of `lib/types/api.ts`**

Add these lines at the end of the file (after the `CompareResponse` interface):

```typescript
// ---- Status page -------------------------------------------------------

/** One row from the fetch_state table. */
export interface FetchStateRow {
  source_id: string;
  source_kind: string;
  last_synced_at: string | null;
  updated_at: string;
}

/**
 * One signal row on the status page.
 * freshness: "ok" | "stale" | "missing" — drives badge colour.
 */
export interface StatusSignal {
  label: string;
  timestamp: string | null; // ISO, already converted to local time
  freshness: "ok" | "stale" | "missing";
  detail: string | null; // e.g. source_kind, delivery value, or null
}

export interface StatusResponse {
  signals: StatusSignal[];
  /** IANA timezone used for local-time conversion. */
  timezone: string;
}
```

- [ ] **Step 2: Run tsc to verify no type errors**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx tsc --noEmit
```

Expected: clean (0 errors)

---

### Task 3: DB query layer

**Files:**
- Create: `apps/web/lib/queries/status.ts`

- [ ] **Step 1: Create the query file**

```typescript
// apps/web/lib/queries/status.ts
import { queryRows } from "@/lib/queries/db";
import { classifyFreshness } from "@/lib/status";
import type { StatusResponse, StatusSignal } from "@/lib/types/api";

// CGM/data freshness — nightly sync cadence + slack
const CGM_THRESHOLD_HOURS = 26;
// Detection recency — only fires on rises; absence isn't failure
const DETECTION_THRESHOLD_HOURS = 24;

interface RawCgmLatest {
  latest_ts: string | null;
}

interface RawFetchState {
  source_id: string;
  source_kind: string;
  last_synced_at: string | null;
  updated_at: string;
}

interface RawDetectionLatest {
  latest_created_at: string | null;
}

interface RawAlertLatest {
  latest_fired_at: string | null;
  delivery: string | null;
}

export async function fetchStatus(
  timezone: string,
): Promise<StatusResponse> {
  const [cgmRows, fetchStateRows, detectionRows, alertRows] = await Promise.all([
    queryRows<RawCgmLatest>(
      `SELECT (MAX(timestamp) AT TIME ZONE $1)::text AS latest_ts FROM cgm`,
      [timezone],
    ),
    queryRows<RawFetchState>(
      `SELECT
        source_id,
        source_kind,
        (last_synced_at AT TIME ZONE $1)::text AS last_synced_at,
        (updated_at AT TIME ZONE $1)::text AS updated_at
      FROM fetch_state
      ORDER BY source_id`,
      [timezone],
    ),
    queryRows<RawDetectionLatest>(
      `SELECT (MAX(created_at) AT TIME ZONE $1)::text AS latest_created_at
       FROM detection_results`,
      [timezone],
    ),
    queryRows<RawAlertLatest>(
      `SELECT
        (fired_at AT TIME ZONE $1)::text AS latest_fired_at,
        delivery
      FROM alerts_sent
      ORDER BY fired_at DESC, id DESC
      LIMIT 1`,
      [timezone],
    ),
  ]);

  const signals: StatusSignal[] = [];

  // 1. CGM data recency
  const latestCgm = cgmRows[0]?.latest_ts ?? null;
  signals.push({
    label: "CGM data",
    timestamp: latestCgm ? latestCgm.slice(0, 16) : null,
    freshness: classifyFreshness(latestCgm, CGM_THRESHOLD_HOURS),
    detail: null,
  });

  // 2. Nightly Tandem sync freshness — one row per source in fetch_state
  if (fetchStateRows.length === 0) {
    signals.push({
      label: "Tandem sync",
      timestamp: null,
      freshness: "missing",
      detail: null,
    });
  } else {
    for (const row of fetchStateRows) {
      signals.push({
        label: `Sync: ${row.source_id}`,
        timestamp: row.last_synced_at ? row.last_synced_at.slice(0, 16) : null,
        freshness: classifyFreshness(row.last_synced_at, CGM_THRESHOLD_HOURS),
        detail: row.source_kind,
      });
    }
  }

  // 3. Live meal-rise detection recency
  const latestDetection = detectionRows[0]?.latest_created_at ?? null;
  signals.push({
    label: "Last detection",
    timestamp: latestDetection ? latestDetection.slice(0, 16) : null,
    freshness: classifyFreshness(latestDetection, DETECTION_THRESHOLD_HOURS),
    detail: null,
  });

  // 4. Latest alert (informational — no threshold drives badge, badge mirrors delivery status)
  const latestAlert = alertRows[0] ?? null;
  signals.push({
    label: "Last alert",
    timestamp: latestAlert?.latest_fired_at
      ? latestAlert.latest_fired_at.slice(0, 16)
      : null,
    // No recency threshold: use delivery as freshness stand-in
    freshness:
      latestAlert === null
        ? "missing"
        : latestAlert.delivery === "sent"
          ? "ok"
          : latestAlert.delivery === "failed"
            ? "stale"
            : "ok", // pending = ok (in-flight)
    detail: latestAlert?.delivery ?? null,
  });

  return { signals, timezone };
}
```

- [ ] **Step 2: Run tsc to verify no type errors**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx tsc --noEmit
```

Expected: clean

---

### Task 4: API route (session-guarded)

**Files:**
- Create: `apps/web/app/api/status/route.ts`

- [ ] **Step 1: Create the route**

```typescript
// apps/web/app/api/status/route.ts
import { fetchStatus } from "@/lib/queries/status";
import { getTimezone } from "@/lib/config";
import { jsonError, jsonOk } from "@/lib/api/route";
import { requireSession } from "@/lib/api/auth";

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  try {
    const data = await fetchStatus(getTimezone());
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load status",
      500,
    );
  }
}
```

- [ ] **Step 2: Verify the api-auth source scan still passes**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx vitest run __tests__/api-auth.test.ts
```

Expected: all 4 tests PASS (source-scan test now includes `/api/status/route.ts` and will find `requireSession`)

- [ ] **Step 3: Run tsc to verify no type errors**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx tsc --noEmit
```

Expected: clean

---

### Task 5: Status page UI

**Files:**
- Create: `apps/web/app/status/page.tsx`

- [ ] **Step 1: Create the page**

```tsx
// apps/web/app/status/page.tsx
"use client";

import { useEffect, useState } from "react";
import type { StatusResponse, StatusSignal } from "@/lib/types/api";

type Freshness = StatusSignal["freshness"];

function FreshnessBadge({ freshness }: { freshness: Freshness }) {
  const cls =
    freshness === "ok"
      ? "bg-green-100 text-green-800"
      : freshness === "stale"
        ? "bg-amber-100 text-amber-800"
        : "bg-red-100 text-red-800";
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {freshness}
    </span>
  );
}

export default function StatusPage() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/status")
      .then((r) => r.json())
      .then((body) => {
        if (body.error) setError(body.error);
        else {
          setError(null);
          setData(body);
        }
      });
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Automation status</h1>
      {error && <p className="text-red-600">{error}</p>}
      {!data && !error && <p className="text-slate-500">Loading…</p>}
      {data && (
        <>
          <p className="text-sm text-slate-500">
            Times shown in {data.timezone}
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-slate-500">
                <th className="py-2">Signal</th>
                <th>Last seen</th>
                <th>Status</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {data.signals.map((s) => (
                <tr key={s.label} className="border-b border-slate-100">
                  <td className="whitespace-nowrap py-2 font-medium text-slate-700">
                    {s.label}
                  </td>
                  <td className="whitespace-nowrap text-slate-600">
                    {s.timestamp ?? "—"}
                  </td>
                  <td>
                    <FreshnessBadge freshness={s.freshness} />
                  </td>
                  <td className="text-slate-500">{s.detail ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Run tsc to verify no type errors**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx tsc --noEmit
```

Expected: clean

---

### Task 6: Nav link

**Files:**
- Modify: `apps/web/components/AppNav.tsx`

- [ ] **Step 1: Add Status to the links array**

In `components/AppNav.tsx`, change the `links` array from:

```typescript
const links = [
  { href: "/", label: "Day" },
  { href: "/heatmap", label: "Heatmap" },
  { href: "/agp", label: "AGP" },
  { href: "/trends", label: "TIR" },
  { href: "/insulin", label: "Insulin" },
  { href: "/search", label: "Search" },
  { href: "/compare", label: "Compare" },
  { href: "/alerts", label: "Alerts" },
];
```

to:

```typescript
const links = [
  { href: "/", label: "Day" },
  { href: "/heatmap", label: "Heatmap" },
  { href: "/agp", label: "AGP" },
  { href: "/trends", label: "TIR" },
  { href: "/insulin", label: "Insulin" },
  { href: "/search", label: "Search" },
  { href: "/compare", label: "Compare" },
  { href: "/alerts", label: "Alerts" },
  { href: "/status", label: "Status" },
];
```

- [ ] **Step 2: Run full vitest suite**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx vitest run
```

Expected: all tests PASS (status.test.ts + api-auth source-scan + all pre-existing tests)

- [ ] **Step 3: Run tsc final check**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine/apps/web && npx tsc --noEmit
```

Expected: clean

- [ ] **Step 4: Commit everything**

```bash
cd /Users/ashtonmeyer-bibbins/Desktop/projects/t1d-engine && git add \
  apps/web/lib/types/api.ts \
  apps/web/lib/queries/status.ts \
  apps/web/app/api/status/route.ts \
  apps/web/app/status/page.tsx \
  apps/web/components/AppNav.tsx && \
git commit -m "$(cat <<'EOF'
feat(web): automation-health status page at /status

Surfaces CGM data recency, Tandem sync freshness per fetch_state row,
last detection_results created_at, and last alerts_sent delivery at a
glance.  Freshness badges mirror the alerts delivery-badge palette
(green ok / amber stale / red missing).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task covering it |
|-------------|----------------|
| `lib/queries/status.ts` — latest `cgm.timestamp` | Task 3 |
| `lib/queries/status.ts` — each `fetch_state` row (source_id, last_synced_at, source_kind, updated_at) | Task 3 |
| `lib/queries/status.ts` — latest `detection_results.created_at` | Task 3 |
| `lib/queries/status.ts` — latest `alerts_sent.fired_at` + delivery | Task 3 |
| `lib/status.ts` pure helper — classifyFreshness returning ok/stale/missing | Task 1 |
| Thresholds: cgm=26h, detection=24h, alerts=informational | Task 3 |
| Vitest tests for classifier in `__tests__/status.test.ts` | Task 1 |
| Types in `lib/types/api.ts` | Task 2 |
| `app/api/status/route.ts` — session-guarded | Task 4 |
| api-auth source-scan still passes | Task 4, Step 2 |
| `app/status/page.tsx` — card/table, badges matching alerts palette | Task 5 |
| Nav link "Status" | Task 6 |
| `npx vitest run` all pass | Task 6, Step 2 |
| `npx tsc --noEmit` clean | Tasks 2, 3, 4, 5, 6 |

**Placeholder scan:** No TBD, no "fill in later", no vague steps.

**Type consistency:**
- `FreshnessStatus` defined in `lib/status.ts`, imported by `lib/queries/status.ts` (via `classifyFreshness` usage — the return type flows through)
- `StatusSignal.freshness` typed as `"ok" | "stale" | "missing"` — consistent with `FreshnessStatus`
- `StatusResponse` used in `lib/queries/status.ts` return type and in `app/status/page.tsx`
- `queryRows<RawCgmLatest>` etc. — raw row interfaces all local to `lib/queries/status.ts`, not exported (not needed outside)
- `FetchStateRow` defined in `lib/types/api.ts` but NOT imported by `lib/queries/status.ts` — that file uses the internal `RawFetchState` interface. This is intentional: the DB internal shape (with timezone-converted strings) is the same as `FetchStateRow` conceptually but the query returns it directly into `StatusSignal` rows, so there's no consumer of `FetchStateRow` as a standalone type. The spec requested it so it's in `lib/types/api.ts` for future use.

All clean.
