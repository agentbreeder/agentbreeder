import { useSearchParams } from "react-router-dom";
import { useCallback, useMemo } from "react";

/**
 * Syncs a piece of state with a URL search parameter.
 *
 * Supports `string`, `number`, and `string[]` values.
 * - Strings are stored / read as-is.
 * - Numbers are stored as strings and parsed on read.
 * - Arrays are stored as comma-separated values.
 *
 * When the value equals `defaultValue`, the parameter is removed
 * from the URL to keep URLs clean.
 */
export function useUrlState<T extends string | number | string[]>(
  key: string,
  defaultValue: T,
): [T, (value: T) => void] {
  const [searchParams, setSearchParams] = useSearchParams();

  const rawValue = searchParams.get(key);

  const value: T = useMemo(() => {
    if (rawValue === null) return defaultValue;

    // string[]
    if (Array.isArray(defaultValue)) {
      return (rawValue ? rawValue.split(",") : []) as T;
    }

    // number
    if (typeof defaultValue === "number") {
      const n = Number(rawValue);
      return (Number.isNaN(n) ? defaultValue : n) as T;
    }

    // string
    return rawValue as T;
  }, [rawValue, defaultValue]);

  const setValue = useCallback(
    (next: T) => {
      setSearchParams(
        (prev) => {
          const sp = new URLSearchParams(prev);

          // Determine the serialised string for `next`.
          let serialised: string;
          if (Array.isArray(next)) {
            serialised = next.join(",");
          } else {
            serialised = String(next);
          }

          // Determine the serialised string for `defaultValue`.
          let defaultSerialised: string;
          if (Array.isArray(defaultValue)) {
            defaultSerialised = (defaultValue as string[]).join(",");
          } else {
            defaultSerialised = String(defaultValue);
          }

          if (serialised === defaultSerialised || serialised === "") {
            sp.delete(key);
          } else {
            sp.set(key, serialised);
          }

          return sp;
        },
        { replace: true },
      );
    },
    [key, setSearchParams, defaultValue],
  );

  return [value, setValue];
}
