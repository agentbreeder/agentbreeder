import type { NodeProps, Node } from "@xyflow/react";
import type { GuardrailNodeData } from "../types";
import { BaseNode } from "./BaseNode";

type GuardrailNode = Node<GuardrailNodeData, "guardrail">;

const GUARDRAIL_LABELS: Record<string, string> = {
  pii_detection: "Strips PII from outputs",
  content_filter: "Blocks harmful content",
  hallucination_check: "Flags low-confidence responses",
};

export function GuardrailNode({ data, selected }: NodeProps<GuardrailNode>) {
  const desc = GUARDRAIL_LABELS[data.guardrailType] ?? data.guardrailType;

  return (
    <BaseNode
      nodeType="guardrail"
      title={data.name || "Guardrail"}
      subtitle={data.guardrailType}
      selected={selected}
      showTarget={true}
      showSource={false}
    >
      <div className="leading-snug text-muted-foreground/80">{desc}</div>
    </BaseNode>
  );
}
