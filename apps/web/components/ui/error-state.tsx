import * as React from "react";
import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface ErrorStateProps extends React.ComponentProps<"div"> {
  title?: string;
  message: string;
  onRetry?: () => void;
}

/**
 * Error surface with an optional retry. Uses role="alert" so assistive tech
 * announces it when it appears mid-flow.
 */
function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  className,
  ...props
}: ErrorStateProps) {
  return (
    <div
      data-slot="error-state"
      role="alert"
      className={cn(
        "flex flex-col items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3",
        className,
      )}
      {...props}
    >
      <div className="flex items-center gap-2 text-destructive">
        <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
        <p className="text-sm font-medium">{title}</p>
      </div>
      <p className="text-sm text-muted-foreground">{message}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export { ErrorState };
