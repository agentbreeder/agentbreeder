/**
 * PropertyPanel — right sidebar that shows editable properties
 * for the currently selected node on the canvas.
 */

import type { Node } from "@xyflow/react";
import { Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type CanvasNodeData,
  type CanvasNodeType,
  type AgentNodeData,
  type ModelNodeData,
  type ToolNodeData,
  type McpServerNodeData,
  type PromptNodeData,
  type MemoryNodeData,
  type RagNodeData,
  type GuardrailNodeData,
  NODE_STYLES,
} from "./types";

interface PropertyPanelProps {
  node: Node<CanvasNodeData> | null;
  onUpdateNode: (id: string, data: Partial<CanvasNodeData>) => void;
  onDeleteNode: (id: string) => void;
  onClose: () => void;
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
      {children}
    </label>
  );
}

function FieldInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <Input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-7 text-xs"
      />
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <Label>{label}</Label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function AgentProperties({
  data,
  onChange,
}: {
  data: AgentNodeData;
  onChange: (patch: Partial<AgentNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="my-agent" />
      <FieldInput label="Version" value={data.version} onChange={(v) => onChange({ version: v })} placeholder="0.1.0" />
      <FieldInput label="Description" value={data.description} onChange={(v) => onChange({ description: v })} placeholder="What does this agent do?" />
      <FieldInput label="Team" value={data.team} onChange={(v) => onChange({ team: v })} placeholder="engineering" />
      <FieldInput label="Owner" value={data.owner} onChange={(v) => onChange({ owner: v })} placeholder="user@example.com" />
      <SelectField
        label="Framework"
        value={data.framework}
        onChange={(v) => onChange({ framework: v })}
        options={[
          { value: "langgraph", label: "LangGraph" },
          { value: "openai_agents", label: "OpenAI Agents" },
          { value: "crewai", label: "CrewAI" },
          { value: "claude_sdk", label: "Claude SDK" },
          { value: "google_adk", label: "Google ADK" },
          { value: "custom", label: "Custom" },
        ]}
      />
      <SelectField
        label="Cloud Target"
        value={data.cloud}
        onChange={(v) => {
          const runtimes: Record<string, string> = {
            local: "docker-compose",
            aws: "ecs-fargate",
            gcp: "cloud-run",
            kubernetes: "deployment",
          };
          onChange({ cloud: v, runtime: runtimes[v] ?? "docker-compose" });
        }}
        options={[
          { value: "local", label: "Local Docker" },
          { value: "aws", label: "AWS ECS Fargate" },
          { value: "gcp", label: "Google Cloud Run" },
          { value: "kubernetes", label: "Kubernetes" },
        ]}
      />
      <div className="grid grid-cols-2 gap-2">
        <FieldInput label="Min Instances" value={data.scalingMin} onChange={(v) => onChange({ scalingMin: parseInt(v) || 1 })} type="number" />
        <FieldInput label="Max Instances" value={data.scalingMax} onChange={(v) => onChange({ scalingMax: parseInt(v) || 10 })} type="number" />
      </div>
    </div>
  );
}

function ModelProperties({
  data,
  onChange,
}: {
  data: ModelNodeData;
  onChange: (patch: Partial<ModelNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Model Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="claude-sonnet-4" />
      <FieldInput label="Provider" value={data.provider} onChange={(v) => onChange({ provider: v })} placeholder="anthropic" />
      <SelectField
        label="Role"
        value={data.role}
        onChange={(v) => onChange({ role: v as "primary" | "fallback" })}
        options={[
          { value: "primary", label: "Primary" },
          { value: "fallback", label: "Fallback" },
        ]}
      />
      <div>
        <Label>Temperature ({data.temperature.toFixed(1)})</Label>
        <input
          type="range"
          min={0}
          max={2}
          step={0.1}
          value={data.temperature}
          onChange={(e) => onChange({ temperature: parseFloat(e.target.value) })}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-muted accent-foreground"
        />
      </div>
      <FieldInput
        label="Max Tokens"
        value={data.maxTokens}
        onChange={(v) => onChange({ maxTokens: parseInt(v) || 4096 })}
        type="number"
      />
    </div>
  );
}

function ToolProperties({
  data,
  onChange,
}: {
  data: ToolNodeData;
  onChange: (patch: Partial<ToolNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="search" />
      <FieldInput label="Registry Ref" value={data.ref} onChange={(v) => onChange({ ref: v })} placeholder="tools/my-tool" />
      <SelectField
        label="Type"
        value={data.toolType}
        onChange={(v) => onChange({ toolType: v })}
        options={[
          { value: "function", label: "Function" },
          { value: "mcp", label: "MCP" },
          { value: "api", label: "API" },
        ]}
      />
    </div>
  );
}

function McpServerProperties({
  data,
  onChange,
}: {
  data: McpServerNodeData;
  onChange: (patch: Partial<McpServerNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="my-mcp-server" />
      <FieldInput label="Endpoint" value={data.endpoint} onChange={(v) => onChange({ endpoint: v })} placeholder="http://localhost:3000" />
      <SelectField
        label="Transport"
        value={data.transport}
        onChange={(v) => onChange({ transport: v as McpServerNodeData["transport"] })}
        options={[
          { value: "stdio", label: "stdio" },
          { value: "sse", label: "SSE" },
          { value: "streamable_http", label: "Streamable HTTP" },
        ]}
      />
    </div>
  );
}

function PromptProperties({
  data,
  onChange,
}: {
  data: PromptNodeData;
  onChange: (patch: Partial<PromptNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="system-prompt" />
      <FieldInput label="Registry Ref" value={data.ref} onChange={(v) => onChange({ ref: v })} placeholder="prompts/support-system-v3" />
      <SelectField
        label="Role"
        value={data.role}
        onChange={(v) => onChange({ role: v as "system" | "user" })}
        options={[
          { value: "system", label: "System" },
          { value: "user", label: "User" },
        ]}
      />
      <div>
        <Label>Content</Label>
        <textarea
          value={data.content}
          onChange={(e) => onChange({ content: e.target.value })}
          placeholder="Enter prompt content or use a registry ref..."
          className="min-h-[100px] w-full resize-y rounded-lg border border-input bg-background p-2 text-xs outline-none placeholder:text-muted-foreground/50 focus:ring-1 focus:ring-ring"
        />
      </div>
    </div>
  );
}

function MemoryProperties({
  data,
  onChange,
}: {
  data: MemoryNodeData;
  onChange: (patch: Partial<MemoryNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="conversation-memory" />
      <SelectField
        label="Backend"
        value={data.backendType}
        onChange={(v) => onChange({ backendType: v })}
        options={[
          { value: "redis", label: "Redis" },
          { value: "postgres", label: "PostgreSQL" },
          { value: "sqlite", label: "SQLite" },
          { value: "in_memory", label: "In-Memory" },
        ]}
      />
      <SelectField
        label="Memory Type"
        value={data.memoryType}
        onChange={(v) => onChange({ memoryType: v })}
        options={[
          { value: "conversation", label: "Conversation" },
          { value: "summary", label: "Summary" },
          { value: "buffer", label: "Buffer" },
        ]}
      />
      <FieldInput
        label="Max Messages"
        value={data.maxMessages}
        onChange={(v) => onChange({ maxMessages: parseInt(v) || 100 })}
        type="number"
      />
    </div>
  );
}

function RagProperties({
  data,
  onChange,
}: {
  data: RagNodeData;
  onChange: (patch: Partial<RagNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="product-docs" />
      <FieldInput label="Registry Ref" value={data.ref} onChange={(v) => onChange({ ref: v })} placeholder="kb/product-docs" />
      <FieldInput label="Embedding Model" value={data.embeddingModel} onChange={(v) => onChange({ embeddingModel: v })} placeholder="text-embedding-3-small" />
    </div>
  );
}

function GuardrailProperties({
  data,
  onChange,
}: {
  data: GuardrailNodeData;
  onChange: (patch: Partial<GuardrailNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <FieldInput label="Name" value={data.name} onChange={(v) => onChange({ name: v })} placeholder="PII Detection" />
      <SelectField
        label="Type"
        value={data.guardrailType}
        onChange={(v) => onChange({ guardrailType: v })}
        options={[
          { value: "pii_detection", label: "PII Detection" },
          { value: "content_filter", label: "Content Filter" },
          { value: "hallucination_check", label: "Hallucination Check" },
          { value: "custom", label: "Custom" },
        ]}
      />
    </div>
  );
}

export function PropertyPanel({
  node,
  onUpdateNode,
  onDeleteNode,
  onClose,
}: PropertyPanelProps) {
  if (!node) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-4 text-center">
        <div className="text-xs text-muted-foreground">
          Select a node on the canvas to edit its properties.
        </div>
      </div>
    );
  }

  const data = node.data;
  const nodeType = data.type as CanvasNodeType;
  const style = NODE_STYLES[nodeType];

  const handleChange = (patch: Partial<CanvasNodeData>) => {
    onUpdateNode(node.id, patch);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div>
          <h3 className="text-xs font-semibold tracking-tight">
            {style.label} Properties
          </h3>
          <div className="mt-0.5 text-[10px] text-muted-foreground">
            {node.id}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
            onClick={() => onDeleteNode(node.id)}
            title="Delete node"
          >
            <Trash2 className="size-3" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 w-6 p-0 text-muted-foreground"
            onClick={onClose}
            title="Close panel"
          >
            <X className="size-3" />
          </Button>
        </div>
      </div>

      {/* Properties */}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {nodeType === "agent" && (
          <AgentProperties data={data as AgentNodeData} onChange={handleChange} />
        )}
        {nodeType === "model" && (
          <ModelProperties data={data as ModelNodeData} onChange={handleChange} />
        )}
        {nodeType === "tool" && (
          <ToolProperties data={data as ToolNodeData} onChange={handleChange} />
        )}
        {nodeType === "mcpServer" && (
          <McpServerProperties data={data as McpServerNodeData} onChange={handleChange} />
        )}
        {nodeType === "prompt" && (
          <PromptProperties data={data as PromptNodeData} onChange={handleChange} />
        )}
        {nodeType === "memory" && (
          <MemoryProperties data={data as MemoryNodeData} onChange={handleChange} />
        )}
        {nodeType === "rag" && (
          <RagProperties data={data as RagNodeData} onChange={handleChange} />
        )}
        {nodeType === "guardrail" && (
          <GuardrailProperties data={data as GuardrailNodeData} onChange={handleChange} />
        )}
      </div>
    </div>
  );
}
