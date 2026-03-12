import type { NodeProps, Node } from "@xyflow/react";
import type { RagNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type RagNode = Node<RagNodeData, "rag">;

export function RagNode({ data, selected }: NodeProps<RagNode>) {
  return (
    <BaseNode
      nodeType="rag"
      title={data.name || "RAG Index"}
      subtitle={data.ref || "kb/..."}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      {data.embeddingModel && (
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground/70">Embedding</span>
          <span className="font-medium text-foreground">{data.embeddingModel}</span>
        </div>
      )}
    </BaseNode>
  );
}
