import type { NodeProps, Node } from "@xyflow/react";
import type { ModelNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type ModelNode = Node<ModelNodeData, "model">;

export function ModelNode({ data, selected }: NodeProps<ModelNode>) {
  return (
    <BaseNode
      nodeType="model"
      title={data.name || "Model"}
      subtitle={data.provider || "provider"}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Role</span>
          <span className="font-medium text-foreground">{data.role}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Temp</span>
          <span className="font-medium text-foreground">{data.temperature}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Max Tokens</span>
          <span className="font-medium text-foreground">{data.maxTokens}</span>
        </div>
      </div>
    </BaseNode>
  );
}
