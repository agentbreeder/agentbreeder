import { useEffect } from "react";

export function useDebouncedEffect(
  effect: () => void,
  deps: unknown[],
  delayMs: number,
): void {
  useEffect(() => {
    const t = setTimeout(effect, delayMs);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, delayMs]);
}
