import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface EmptyStateAction {
  label: string;
  onClick: () => void;
}

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: EmptyStateAction;
  className?: string;
}

function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        "flex flex-col items-center justify-center px-4 py-24 text-center",
        className
      )}
    >
      {Icon && (
        <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
          <Icon className="size-5 text-muted-foreground" />
        </div>
      )}
      <h3 className="text-sm font-medium">{title}</h3>
      {description && (
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
          {description}
        </p>
      )}
      {action && (
        <Button size="sm" className="mt-4" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}

export { EmptyState };
export type { EmptyStateProps, EmptyStateAction };
