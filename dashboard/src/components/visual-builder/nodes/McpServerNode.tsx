import type { NodeProps, Node } from "@xyflow/react";
import type { McpServerNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type McpServerNode = Node<McpServerNodeData, "mcpServer">;

export function McpServerNode({ data, selected }: NodeProps<McpServerNode>) {
  return (
    <BaseNode
      nodeType="mcpServer"
      title={data.name || "MCP Server"}
      subtitle={data.transport || "stdio"}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      {data.endpoint && (
        <div className="truncate font-mono text-[9px] text-muted-foreground/80">
          {data.endpoint}
        </div>
      )}
    </BaseNode>
  );
}
