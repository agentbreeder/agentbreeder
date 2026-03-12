import { useMemo, useCallback } from "react";
import { useUrlState } from "@/hooks/use-url-state";

export type SortDirection = "asc" | "desc";

interface UseSortableResult<T> {
  sortedData: T[];
  sortKey: string;
  sortDirection: SortDirection;
  toggleSort: (key: string) => void;
}

/**
 * Sort an array of objects by a given key, persisting sort state in URL params.
 *
 * Supports string, number, and ISO date-string sorting.
 */
function useSortable<T extends Record<string, unknown>>(
  data: T[],
  defaultSort: string,
  defaultDirection: SortDirection = "asc"
): UseSortableResult<T> {
  const [sortKey, setSortKey] = useUrlState("sort", defaultSort);
  const [dirStr, setDir] = useUrlState("dir", defaultDirection);
  const sortDirection: SortDirection = dirStr === "desc" ? "desc" : "asc";

  const toggleSort = useCallback(
    (key: string) => {
      if (key === sortKey) {
        // Toggle direction
        setDir(sortDirection === "asc" ? "desc" : "asc");
      } else {
        setSortKey(key);
        setDir("asc");
      }
    },
    [sortKey, sortDirection, setSortKey, setDir]
  );

  const sortedData = useMemo(() => {
    if (!sortKey) return data;

    return [...data].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];

      // Nulls always last
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      let comparison = 0;

      if (typeof aVal === "number" && typeof bVal === "number") {
        comparison = aVal - bVal;
      } else if (typeof aVal === "string" && typeof bVal === "string") {
        // Try date comparison for ISO strings
        if (isIsoDate(aVal) && isIsoDate(bVal)) {
          comparison = new Date(aVal).getTime() - new Date(bVal).getTime();
        } else {
          comparison = aVal.localeCompare(bVal, undefined, {
            numeric: true,
            sensitivity: "base",
          });
        }
      } else {
        comparison = String(aVal).localeCompare(String(bVal));
      }

      return sortDirection === "desc" ? -comparison : comparison;
    });
  }, [data, sortKey, sortDirection]);

  return { sortedData, sortKey, sortDirection, toggleSort };
}

/** Quick heuristic check for ISO 8601 date strings. */
function isIsoDate(value: string): boolean {
  if (value.length < 10) return false;
  // Matches patterns like "2024-01-15" or "2024-01-15T10:30:00Z"
  return /^\d{4}-\d{2}-\d{2}/.test(value);
}

export { useSortable };
