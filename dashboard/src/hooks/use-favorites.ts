import { useState, useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "ag-favorites";

/** Read favorites set from localStorage. */
function getSnapshot(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

function persist(ids: Set<string>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
  // Dispatch a custom event so other hook instances re-sync
  window.dispatchEvent(new Event("ag-favorites-change"));
}

/** Subscribe to favorites changes (cross-component sync). */
function subscribe(cb: () => void) {
  window.addEventListener("ag-favorites-change", cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener("ag-favorites-change", cb);
    window.removeEventListener("storage", cb);
  };
}

let cachedSnapshot = getSnapshot();

function getExternalSnapshot(): Set<string> {
  const next = getSnapshot();
  // Shallow compare — only create a new reference if contents changed
  if (
    next.size === cachedSnapshot.size &&
    [...next].every((id) => cachedSnapshot.has(id))
  ) {
    return cachedSnapshot;
  }
  cachedSnapshot = next;
  return cachedSnapshot;
}

/**
 * Hook to manage favorite/bookmarked resources.
 * State is stored in localStorage under "ag-favorites" and synced across components.
 */
export function useFavorites() {
  const favorites = useSyncExternalStore(subscribe, getExternalSnapshot);
  const [, forceUpdate] = useState(0);

  const isFavorite = useCallback(
    (id: string) => favorites.has(id),
    [favorites]
  );

  const toggleFavorite = useCallback(
    (id: string) => {
      const next = new Set(favorites);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      persist(next);
      forceUpdate((n) => n + 1);
    },
    [favorites]
  );

  const showOnlyFavorites = useCallback(
    <T extends { id: string }>(items: T[]): T[] =>
      items.filter((item) => favorites.has(item.id)),
    [favorites]
  );

  return {
    favorites,
    isFavorite,
    toggleFavorite,
    showOnlyFavorites,
    count: favorites.size,
  };
}
