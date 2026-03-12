import type { NodeProps, Node } from "@xyflow/react";
import type { MemoryNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type MemoryNode = Node<MemoryNodeData, "memory">;

export function MemoryNode({ data, selected }: NodeProps<MemoryNode>) {
  return (
    <BaseNode
      nodeType="memory"
      title={data.name || "Memory"}
      subtitle={data.backendType || "redis"}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Type</span>
          <span className="font-medium text-foreground">{data.memoryType || "conversation"}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Max Msgs</span>
          <span className="font-medium text-foreground">{data.maxMessages}</span>
        </div>
      </div>
    </BaseNode>
  );
}
