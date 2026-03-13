import { Merge } from "lucide-react";
import type { OrchNodeData } from "./types";

export function MergeNode({ data }: { data: OrchNodeData }) {
  return (
    <div className="rounded-lg border-2 border-emerald-300 bg-emerald-50 dark:bg-emerald-950/30 px-4 py-3 min-w-[140px]">
      <div className="flex items-center gap-2">
        <Merge className="size-4 text-emerald-600" />
        <span className="text-sm font-medium">{data.label}</span>
      </div>
      <p className="text-xs text-muted-foreground mt-1">Merge / Synthesize</p>
    </div>
  );
}
