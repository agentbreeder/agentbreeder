import type { NodeProps, Node } from "@xyflow/react";
import type { ToolNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type ToolNode = Node<ToolNodeData, "tool">;

export function ToolNode({ data, selected }: NodeProps<ToolNode>) {
  return (
    <BaseNode
      nodeType="tool"
      title={data.name || "Tool"}
      subtitle={data.ref || "tools/..."}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      {data.toolType && (
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Type</span>
          <span className="font-medium text-foreground">{data.toolType}</span>
        </div>
      )}
    </BaseNode>
  );
}
