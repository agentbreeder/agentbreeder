import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const TAG_COLORS = [
  "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
  "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20",
];

/** Deterministic color for a tag based on its string hash. */
function tagColor(tag: string): string {
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = (hash << 5) - hash + tag.charCodeAt(i);
    hash |= 0;
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

interface TagBadgeProps {
  tag: string;
  onRemove?: () => void;
  onClick?: () => void;
  active?: boolean;
  className?: string;
}

/** A single tag badge with optional remove button and click handler. */
export function TagBadge({ tag, onRemove, onClick, active, className }: TagBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors",
        tagColor(tag),
        onClick && "cursor-pointer hover:opacity-80",
        active && "ring-1 ring-foreground/30",
        className
      )}
      onClick={onClick}
    >
      {tag}
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-foreground/10"
        >
          <X className="size-2.5" />
        </button>
      )}
    </span>
  );
}

interface TagInputProps {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  className?: string;
}

/**
 * Reusable tag input component.
 * Displays existing tags as removable badges with a text input to add new ones.
 * Press Enter or comma to add a tag. Backspace removes the last tag when input is empty.
 */
export function TagInput({
  tags,
  onChange,
  placeholder = "Add tag...",
  className,
}: TagInputProps) {
  const [input, setInput] = useState("");

  const addTag = (raw: string) => {
    const tag = raw.trim().toLowerCase();
    if (tag && !tags.includes(tag)) {
      onChange([...tags, tag]);
    }
    setInput("");
  };

  const removeTag = (tag: string) => {
    onChange(tags.filter((t) => t !== tag));
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(input);
    } else if (e.key === "Backspace" && input === "" && tags.length > 0) {
      removeTag(tags[tags.length - 1]);
    }
  };

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1.5",
        className
      )}
    >
      {tags.map((tag) => (
        <TagBadge key={tag} tag={tag} onRemove={() => removeTag(tag)} />
      ))}
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => {
          if (input.trim()) addTag(input);
        }}
        placeholder={tags.length === 0 ? placeholder : ""}
        className="min-w-[80px] flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}

interface TagFilterProps {
  allTags: string[];
  activeTags: string[];
  onToggle: (tag: string) => void;
  className?: string;
}

/** A row of clickable tag badges for filtering. */
export function TagFilter({ allTags, activeTags, onToggle, className }: TagFilterProps) {
  if (allTags.length === 0) return null;

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {allTags.map((tag) => (
        <TagBadge
          key={tag}
          tag={tag}
          onClick={() => onToggle(tag)}
          active={activeTags.includes(tag)}
        />
      ))}
    </div>
  );
}
