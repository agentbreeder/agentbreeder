import { cn } from "@/lib/utils";

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

function SkeletonText({
  lines = 3,
  className,
  ...props
}: React.ComponentProps<"div"> & { lines?: number }) {
  // Varying widths for realism
  const widths = ["w-full", "w-4/5", "w-3/5", "w-5/6", "w-2/3"];

  return (
    <div
      data-slot="skeleton-text"
      className={cn("space-y-2", className)}
      {...props}
    >
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn("h-4", widths[i % widths.length])}
        />
      ))}
    </div>
  );
}

function SkeletonCard({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton-card"
      className={cn(
        "rounded-xl bg-card p-4 ring-1 ring-foreground/10",
        className
      )}
      {...props}
    >
      {/* Header */}
      <div className="flex items-center gap-3">
        <Skeleton className="size-8 rounded-lg" />
        <div className="flex-1 space-y-1.5">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
      {/* Body lines */}
      <div className="mt-4">
        <SkeletonText lines={3} />
      </div>
    </div>
  );
}

function SkeletonTable({
  rows = 5,
  columns = 4,
  className,
  ...props
}: React.ComponentProps<"div"> & { rows?: number; columns?: number }) {
  return (
    <div
      data-slot="skeleton-table"
      className={cn("w-full", className)}
      {...props}
    >
      {/* Header row */}
      <div className="flex gap-4 border-b border-border pb-3">
        {Array.from({ length: columns }).map((_, i) => (
          <Skeleton
            key={`header-${i}`}
            className={cn(
              "h-4",
              i === 0 ? "w-1/4" : i === columns - 1 ? "w-1/6" : "w-1/5"
            )}
          />
        ))}
      </div>
      {/* Data rows */}
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div
          key={`row-${rowIdx}`}
          className="flex gap-4 border-b border-border py-3 last:border-0"
        >
          {Array.from({ length: columns }).map((_, colIdx) => (
            <Skeleton
              key={`cell-${rowIdx}-${colIdx}`}
              className={cn(
                "h-4",
                colIdx === 0
                  ? "w-1/4"
                  : colIdx === columns - 1
                    ? "w-1/6"
                    : "w-1/5"
              )}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export { Skeleton, SkeletonCard, SkeletonTable, SkeletonText };
