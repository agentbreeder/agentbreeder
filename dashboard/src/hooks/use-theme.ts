import { useEffect } from "react";

// Studio is dark-only, matching agentbreeder.io. Kept as a hook so existing
// imports keep working; it simply guarantees the `dark` class is present.
export function useTheme() {
  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);
  return { theme: "dark" as const, resolved: "dark" as const, setTheme: () => {} };
}
