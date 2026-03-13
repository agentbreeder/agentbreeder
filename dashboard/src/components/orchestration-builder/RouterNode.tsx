import { GitBranch } from "lucide-react";
import type { OrchNodeData } from "./types";

export function RouterNode({ data }: { data: OrchNodeData }) {
  return (
    <div className="rounded-lg border-2 border-amber-300 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 min-w-[140px]">
      <div className="flex items-center gap-2">
        <GitBranch className="size-4 text-amber-600" />
        <span className="text-sm font-medium">{data.label}</span>
      </div>
      {data.routes && <p className="text-xs text-muted-foreground mt-1">{data.routes.length} rules</p>}
    </div>
  );
}
