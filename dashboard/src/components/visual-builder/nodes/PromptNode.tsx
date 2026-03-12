import type { NodeProps, Node } from "@xyflow/react";
import type { PromptNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type PromptNode = Node<PromptNodeData, "prompt">;

export function PromptNode({ data, selected }: NodeProps<PromptNode>) {
  const preview = data.content
    ? data.content.slice(0, 60) + (data.content.length > 60 ? "..." : "")
    : data.ref || "No content";

  return (
    <BaseNode
      nodeType="prompt"
      title={data.name || "Prompt"}
      subtitle={data.role}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      <div className="italic leading-snug text-muted-foreground/80">
        {preview}
      </div>
    </BaseNode>
  );
}
