import { useState, useMemo, useCallback } from "react";
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { TableHead } from "@/components/ui/table";

export type SortDirection = "asc" | "desc" | null;

interface SortableHeaderProps {
  children: React.ReactNode;
  sortKey: string;
  currentSortKey: string | null;
  currentDirection: SortDirection;
  onSort: (key: string) => void;
  className?: string;
}

function SortableHeader({
  children,
  sortKey,
  currentSortKey,
  currentDirection,
  onSort,
  className,
}: SortableHeaderProps) {
  const isActive = currentSortKey === sortKey;

  return (
    <TableHead className={cn("select-none", className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "inline-flex items-center gap-1.5 transition-colors hover:text-foreground",
          isActive ? "text-foreground" : "text-muted-foreground"
        )}
      >
        {children}
        {isActive && currentDirection === "asc" && (
          <ArrowUp className="size-3.5" />
        )}
        {isActive && currentDirection === "desc" && (
          <ArrowDown className="size-3.5" />
        )}
        {!isActive && <ArrowUpDown className="size-3.5 opacity-40" />}
      </button>
    </TableHead>
  );
}

interface UseSortedDataResult<T> {
  sortedData: T[];
  sortKey: string | null;
  sortDirection: SortDirection;
  requestSort: (key: string) => void;
}

function useSortedData<T extends Record<string, unknown>>(
  data: T[],
  defaultSortKey: string | null = null,
  defaultDirection: SortDirection = null
): UseSortedDataResult<T> {
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey);
  const [sortDirection, setSortDirection] =
    useState<SortDirection>(defaultDirection);

  const requestSort = useCallback(
    (key: string) => {
      if (sortKey !== key) {
        // New column: start ascending
        setSortKey(key);
        setSortDirection("asc");
      } else {
        // Same column: cycle asc → desc → none
        if (sortDirection === "asc") {
          setSortDirection("desc");
        } else if (sortDirection === "desc") {
          setSortKey(null);
          setSortDirection(null);
        } else {
          setSortDirection("asc");
        }
      }
    },
    [sortKey, sortDirection]
  );

  const sortedData = useMemo(() => {
    if (!sortKey || !sortDirection) return data;

    return [...data].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];

      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      let comparison = 0;
      if (typeof aVal === "string" && typeof bVal === "string") {
        comparison = aVal.localeCompare(bVal);
      } else if (typeof aVal === "number" && typeof bVal === "number") {
        comparison = aVal - bVal;
      } else {
        comparison = String(aVal).localeCompare(String(bVal));
      }

      return sortDirection === "desc" ? -comparison : comparison;
    });
  }, [data, sortKey, sortDirection]);

  return { sortedData, sortKey, sortDirection, requestSort };
}

export { SortableHeader, useSortedData };
export type { SortableHeaderProps, UseSortedDataResult };
