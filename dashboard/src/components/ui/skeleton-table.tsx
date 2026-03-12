import { cn } from "@/lib/utils";

interface SkeletonTableRowsProps {
  /** Number of skeleton rows to display. */
  rows?: number;
  /** Number of columns per row. */
  columns?: number;
  /** Optional class name for each row container. */
  rowClassName?: string;
  className?: string;
}

/**
 * Animated skeleton rows matching the table-row list page layout.
 * Renders inside the existing `<div>` table container (no `<table>` element).
 *
 * Uses `animate-pulse` for a subtle shimmer.
 */
function SkeletonTableRows({
  rows = 8,
  columns = 5,
  rowClassName,
  className,
}: SkeletonTableRowsProps) {
  // Vary widths across columns for a realistic look
  const colWidths = [
    "w-36",   // name
    "w-56",   // description
    "w-16",   // badge
    "w-14",   // secondary
    "w-10",   // timestamp
    "w-12",
    "w-20",
  ];

  return (
    <div data-slot="skeleton-table-rows" className={cn("space-y-0", className)}>
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div
          key={rowIdx}
          className={cn(
            "flex items-center gap-4 border-b border-border/50 px-5 py-3.5 last:border-0",
            rowClassName
          )}
        >
          {/* Leading dot / icon placeholder */}
          <div className="size-2 animate-pulse rounded-full bg-muted" />

          {/* Main content area */}
          <div className="flex-1 space-y-1.5">
            <div
              className={cn(
                "h-3.5 animate-pulse rounded bg-muted",
                colWidths[0]
              )}
            />
            <div
              className={cn(
                "h-2.5 animate-pulse rounded bg-muted/60",
                colWidths[1]
              )}
            />
          </div>

          {/* Additional columns */}
          {Array.from({ length: Math.max(0, columns - 2) }).map((_, colIdx) => (
            <div
              key={colIdx}
              className={cn(
                "h-3.5 animate-pulse rounded bg-muted",
                colWidths[(colIdx + 2) % colWidths.length]
              )}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

interface SkeletonCardGridProps {
  /** Number of skeleton cards to render. */
  cards?: number;
  /** Grid columns class, e.g. "md:grid-cols-2". */
  gridClassName?: string;
  className?: string;
}

/**
 * Skeleton loading state for card-grid pages (tools, prompts).
 */
function SkeletonCardGrid({
  cards = 4,
  gridClassName = "md:grid-cols-2",
  className,
}: SkeletonCardGridProps) {
  return (
    <div
      data-slot="skeleton-card-grid"
      className={cn("grid gap-3", gridClassName, className)}
    >
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="rounded-lg border border-border p-4">
          <div className="flex items-start gap-3">
            <div className="size-9 animate-pulse rounded-lg bg-muted" />
            <div className="flex-1 space-y-2">
              <div className="h-4 w-32 animate-pulse rounded bg-muted" />
              <div className="h-3 w-48 animate-pulse rounded bg-muted/60" />
              <div className="h-3 w-24 animate-pulse rounded bg-muted/40" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export { SkeletonTableRows, SkeletonCardGrid };
export type { SkeletonTableRowsProps, SkeletonCardGridProps };
