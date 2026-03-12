import { Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { useFavorites } from "@/hooks/use-favorites";

interface FavoriteButtonProps {
  id: string;
  className?: string;
}

/**
 * Star/bookmark toggle button for any resource.
 * Uses the shared useFavorites hook backed by localStorage.
 */
export function FavoriteButton({ id, className }: FavoriteButtonProps) {
  const { isFavorite, toggleFavorite } = useFavorites();
  const starred = isFavorite(id);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleFavorite(id);
      }}
      className={cn(
        "shrink-0 rounded p-0.5 transition-colors hover:bg-muted",
        className
      )}
      title={starred ? "Remove from favorites" : "Add to favorites"}
    >
      <Star
        className={cn(
          "size-3.5 transition-colors",
          starred
            ? "fill-amber-400 text-amber-400"
            : "text-muted-foreground hover:text-foreground"
        )}
      />
    </button>
  );
}
