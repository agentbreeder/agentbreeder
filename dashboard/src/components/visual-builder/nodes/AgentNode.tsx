import type { NodeProps, Node } from "@xyflow/react";
import type { AgentNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type AgentNode = Node<AgentNodeData, "agent">;

export function AgentNode({ data, selected }: NodeProps<AgentNode>) {
  return (
    <BaseNode
      nodeType="agent"
      title={data.name || "My Agent"}
      subtitle={`v${data.version || "0.1.0"}`}
      selected={selected}
      showTarget={false}
      showSource={true}
    >
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Framework</span>
          <span className="font-medium text-foreground">{data.framework || "langgraph"}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Team</span>
          <span className="font-medium text-foreground">{data.team || "engineering"}</span>
        </div>
        {data.cloud && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground/70">Deploy</span>
            <span className="font-medium text-foreground">{data.cloud}</span>
          </div>
        )}
      </div>
    </BaseNode>
  );
}
