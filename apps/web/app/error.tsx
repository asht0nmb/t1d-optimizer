"use client";

import { useEffect } from "react";
import { ErrorState } from "@/components/ui/error-state";

/**
 * Next.js App Router error boundary for everything under `app/`. Catches
 * render-time throws that a page's own try/catch around its data fetch
 * cannot — without this, those fall through to Next's default unstyled
 * error screen instead of the app's design system.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-lg py-12">
      <ErrorState
        title="Something went wrong"
        message="This page hit an unexpected error. Try again, or use the nav above to head back to the dashboard."
        onRetry={reset}
      />
    </div>
  );
}
