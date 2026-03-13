import { useState, useCallback } from "react";
import { Code, Eye, Trash2, Bot, Shield, Merge } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StrategySelector } from "@/components/orchestration-builder/StrategySelector";
import { RoutingRuleEditor } from "@/components/orchestration-builder/RoutingRuleEditor";
import { AgentNode } from "@/components/orchestration-builder/AgentNode";
import { SupervisorNode } from "@/components/orchestration-builder/SupervisorNode";
import { MergeNode } from "@/components/orchestration-builder/MergeNode";
import { orchestrationGraphToYaml } from "@/lib/orchestration-graph-to-yaml";
import type { OrchestrationStrategy, OrchNode, OrchNodeData } from "@/components/orchestration-builder/types";

function NodeRenderer({ node }: { node: OrchNode }) {
  const { data } = node;
  if (data.type === "supervisor") return <SupervisorNode data={data} />;
  if (data.type === "merge") return <MergeNode data={data} />;
  return <AgentNode data={data} />;
}

export default function OrchestrationBuilderPage() {
  const [name, setName] = useState("my-orchestration");
  const [version, setVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");
  const [strategy, setStrategy] = useState<OrchestrationStrategy>("sequential");
  const [nodes, setNodes] = useState<OrchNode[]>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [showYaml, setShowYaml] = useState(false);

  const addNode = useCallback((type: OrchNodeData["type"]) => {
    const id = `node-${Date.now()}`;
    const label = type === "supervisor" ? "supervisor" : type === "merge" ? "merger" : `agent-${nodes.length + 1}`;
    setNodes((prev) => [
      ...prev,
      {
        id,
        type,
        position: { x: 100 + prev.length * 200, y: 200 },
        data: { label, type, ref: `agents/${label}`, routes: [], description: "" },
      },
    ]);
  }, [nodes.length]);

  const removeNode = useCallback((id: string) => {
    setNodes((prev) => prev.filter((n) => n.id !== id));
    if (selectedNode === id) setSelectedNode(null);
  }, [selectedNode]);

  const updateNodeData = useCallback((id: string, updates: Partial<OrchNodeData>) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...updates } } : n))
    );
  }, []);

  const selected = nodes.find((n) => n.id === selectedNode);
  const agentNames = nodes.map((n) => n.data.label);

  const graph = {
    nodes,
    edges: [],
    strategy,
    name,
    version,
    description,
  };
  const yamlOutput = orchestrationGraphToYaml(graph);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Orchestration Builder</h1>
          <p className="text-muted-foreground">Design multi-agent workflows visually</p>
        </div>
        <div className="flex gap-2">
          <button
            className={`flex items-center gap-1 rounded px-3 py-1.5 text-sm ${showYaml ? "bg-primary text-primary-foreground" : "border hover:bg-muted"}`}
            onClick={() => setShowYaml(!showYaml)}
          >
            {showYaml ? <Eye className="size-4" /> : <Code className="size-4" />}
            {showYaml ? "Canvas" : "View YAML"}
          </button>
        </div>
      </div>

      {/* Config header */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <Input placeholder="Version" value={version} onChange={(e) => setVersion(e.target.value)} />
        <Input placeholder="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>

      {/* Strategy selector */}
      <div>
        <h2 className="mb-2 font-medium text-sm">Strategy</h2>
        <StrategySelector value={strategy} onChange={setStrategy} />
      </div>

      {showYaml ? (
        <div className="rounded-lg border p-4">
          <h2 className="mb-2 font-medium">Generated YAML</h2>
          <pre className="rounded bg-muted p-4 text-sm overflow-auto max-h-96 font-mono">{yamlOutput}</pre>
        </div>
      ) : (
        <>
          {/* Node palette */}
          <div className="flex gap-2">
            <button className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm hover:bg-muted" onClick={() => addNode("agent")}>
              <Bot className="size-4" /> Add Agent
            </button>
            {(strategy === "supervisor" || strategy === "hierarchical") && (
              <button className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm hover:bg-muted" onClick={() => addNode("supervisor")}>
                <Shield className="size-4" /> Add Supervisor
              </button>
            )}
            {strategy === "fan_out_fan_in" && (
              <button className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm hover:bg-muted" onClick={() => addNode("merge")}>
                <Merge className="size-4" /> Add Merger
              </button>
            )}
          </div>

          {/* Canvas */}
          <div className="grid gap-4 md:grid-cols-3">
            <div className="md:col-span-2 rounded-lg border bg-muted/20 p-4 min-h-[400px]">
              {nodes.length === 0 ? (
                <div className="flex h-full items-center justify-center text-muted-foreground">
                  <p>Add agents to start building your orchestration</p>
                </div>
              ) : (
                <div className="flex flex-wrap gap-3">
                  {nodes.map((node) => (
                    <button
                      key={node.id}
                      className={`relative ${selectedNode === node.id ? "ring-2 ring-primary rounded-lg" : ""}`}
                      onClick={() => setSelectedNode(node.id)}
                    >
                      <NodeRenderer node={node} />
                      <button
                        className="absolute -right-1 -top-1 rounded-full bg-red-500 p-0.5 text-white opacity-0 group-hover:opacity-100 hover:opacity-100"
                        onClick={(e) => { e.stopPropagation(); removeNode(node.id); }}
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Property panel */}
            <div className="rounded-lg border p-4">
              <h3 className="mb-3 font-medium text-sm">Properties</h3>
              {selected ? (
                <div className="space-y-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Label</label>
                    <Input value={selected.data.label} onChange={(e) => updateNodeData(selected.id, { label: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Registry Ref</label>
                    <Input value={selected.data.ref ?? ""} onChange={(e) => updateNodeData(selected.id, { ref: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Fallback</label>
                    <Input
                      placeholder="Fallback agent name"
                      value={selected.data.fallback ?? ""}
                      onChange={(e) => updateNodeData(selected.id, { fallback: e.target.value || undefined })}
                    />
                  </div>
                  {strategy === "router" && (
                    <RoutingRuleEditor
                      rules={selected.data.routes ?? []}
                      onChange={(routes) => updateNodeData(selected.id, { routes })}
                      availableTargets={agentNames.filter((n) => n !== selected.data.label)}
                    />
                  )}
                  <Badge variant="outline">{selected.data.type}</Badge>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Select a node to edit properties</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
