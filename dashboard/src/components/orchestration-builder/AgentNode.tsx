import { Bot } from "lucide-react";
import type { OrchNodeData } from "./types";

export function AgentNode({ data }: { data: OrchNodeData }) {
  return (
    <div className="rounded-lg border-2 border-blue-300 bg-blue-50 dark:bg-blue-950/30 px-4 py-3 min-w-[140px]">
      <div className="flex items-center gap-2">
        <Bot className="size-4 text-blue-600" />
        <span className="text-sm font-medium">{data.label}</span>
      </div>
      {data.ref && <p className="text-xs text-muted-foreground mt-1">{data.ref}</p>}
    </div>
  );
}
