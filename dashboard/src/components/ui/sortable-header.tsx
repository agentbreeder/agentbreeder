import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SortDirection } from "@/hooks/use-sortable";

interface SortableColumnHeaderProps {
  children: React.ReactNode;
  /** The key this column sorts by. */
  sortKey: string;
  /** Currently active sort key. */
  currentSortKey: string;
  /** Current sort direction. */
  currentDirection: SortDirection;
  /** Called when the user clicks to toggle sorting. */
  onSort: (key: string) => void;
  className?: string;
}

/**
 * A lightweight clickable header cell that shows directional sort arrows.
 * Designed to work inside the existing `<div>` based table headers
 * (not the `<th>` / TableHead component).
 */
function SortableColumnHeader({
  children,
  sortKey,
  currentSortKey,
  currentDirection,
  onSort,
  className,
}: SortableColumnHeaderProps) {
  const isActive = currentSortKey === sortKey;

  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className={cn(
        "inline-flex items-center gap-1 transition-colors hover:text-foreground",
        isActive ? "text-foreground" : "text-muted-foreground",
        className
      )}
    >
      {children}
      {isActive && currentDirection === "asc" && (
        <ArrowUp className="size-3" />
      )}
      {isActive && currentDirection === "desc" && (
        <ArrowDown className="size-3" />
      )}
      {!isActive && <ArrowUpDown className="size-3 opacity-40" />}
    </button>
  );
}

export { SortableColumnHeader };
export type { SortableColumnHeaderProps };
