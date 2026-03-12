import { cn } from "@/lib/utils";

interface SkeletonDetailProps {
  /** Number of content rows in the body area. */
  bodyRows?: number;
  /** Number of tab placeholders. */
  tabs?: number;
  className?: string;
}

/**
 * Skeleton loading state for detail pages (agent-detail, tool-detail, etc.).
 * Renders a fake header area with back button, title, badges,
 * tab bar, and content placeholder.
 */
function SkeletonDetail({
  bodyRows = 6,
  tabs = 3,
  className,
}: SkeletonDetailProps) {
  return (
    <div
      data-slot="skeleton-detail"
      className={cn("mx-auto max-w-5xl p-6", className)}
    >
      {/* Back link */}
      <div className="mb-6 h-4 w-20 animate-pulse rounded bg-muted" />

      {/* Header */}
      <div className="mb-6 flex items-start justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="h-6 w-48 animate-pulse rounded bg-muted" />
            <div className="h-5 w-14 animate-pulse rounded-full bg-muted" />
            <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
          </div>
          <div className="h-4 w-72 animate-pulse rounded bg-muted/60" />
        </div>
        <div className="flex gap-2">
          <div className="h-8 w-20 animate-pulse rounded-md bg-muted" />
          <div className="h-8 w-20 animate-pulse rounded-md bg-muted" />
        </div>
      </div>

      {/* Meta row */}
      <div className="mb-6 flex gap-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-1">
            <div className="h-3 w-12 animate-pulse rounded bg-muted/50" />
            <div className="h-4 w-20 animate-pulse rounded bg-muted" />
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="mb-4 flex gap-1 border-b border-border">
        {Array.from({ length: tabs }).map((_, i) => (
          <div
            key={i}
            className={cn(
              "h-8 animate-pulse rounded-t-md px-3",
              i === 0 ? "w-20 bg-muted" : "w-16 bg-muted/50"
            )}
          />
        ))}
      </div>

      {/* Body content */}
      <div className="space-y-4">
        {Array.from({ length: bodyRows }).map((_, i) => (
          <div key={i} className="flex gap-4">
            <div className="h-4 w-24 animate-pulse rounded bg-muted/50" />
            <div
              className={cn(
                "h-4 animate-pulse rounded bg-muted",
                i % 3 === 0 ? "w-64" : i % 3 === 1 ? "w-48" : "w-56"
              )}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export { SkeletonDetail };
export type { SkeletonDetailProps };
