import { useState, useCallback, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { Code, Eye, Trash2, Bot, Shield, Merge, Save, Rocket, CheckCircle2, AlertCircle, ListOrdered } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StrategySelector } from "@/components/orchestration-builder/StrategySelector";
import { RoutingRuleEditor } from "@/components/orchestration-builder/RoutingRuleEditor";
import { AgentNode } from "@/components/orchestration-builder/AgentNode";
import { SupervisorNode } from "@/components/orchestration-builder/SupervisorNode";
import { MergeNode } from "@/components/orchestration-builder/MergeNode";
import { orchestrationGraphToYaml } from "@/lib/orchestration-graph-to-yaml";
import {
  api,
  type Orchestration,
  type OrchestrationAgentRef,
  type OrchestrationCreate,
  type OrchestrationValidationError,
} from "@/lib/api";
import type { OrchestrationStrategy, OrchNode, OrchNodeData } from "@/components/orchestration-builder/types";
import { useToast } from "@/hooks/use-toast";

function NodeRenderer({ node }: { node: OrchNode }) {
  const { data } = node;
  if (data.type === "supervisor") return <SupervisorNode data={data} />;
  if (data.type === "merge") return <MergeNode data={data} />;
  return <AgentNode data={data} />;
}

function buildAgentsConfig(nodes: OrchNode[]): Record<string, OrchestrationAgentRef> {
  const agents: Record<string, OrchestrationAgentRef> = {};
  for (const node of nodes) {
    const ref: OrchestrationAgentRef = {
      ref: node.data.ref || `agents/${node.data.label}`,
    };
    if (node.data.routes && node.data.routes.length > 0) ref.routes = node.data.routes;
    if (node.data.fallback) ref.fallback = node.data.fallback;
    agents[node.data.label] = ref;
  }
  return agents;
}

function buildLayout(nodes: OrchNode[]): Record<string, { x: number; y: number }> {
  return Object.fromEntries(nodes.map((n) => [n.id, n.position]));
}

function nodesFromOrchestration(orch: Orchestration): OrchNode[] {
  const layout = orch.layout ?? {};
  const nodes: OrchNode[] = [];
  let i = 0;
  for (const [label, agentRef] of Object.entries(orch.agents_config)) {
    const id = `node-${label}-${i}`;
    const pos = layout[id] ?? { x: 100 + i * 200, y: 200 };
    // Heuristic: derive node type from name when round-tripping.
    let type: OrchNodeData["type"] = "agent";
    if (label.includes("supervisor")) type = "supervisor";
    else if (label.includes("merger") || label.includes("merge")) type = "merge";
    nodes.push({
      id,
      type,
      position: pos,
      data: {
        label,
        type,
        ref: agentRef.ref,
        routes: agentRef.routes ?? [],
        fallback: agentRef.fallback,
        description: "",
      },
    });
    i += 1;
  }
  return nodes;
}

export default function OrchestrationBuilderPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const orchId = searchParams.get("id");

  const [name, setName] = useState("my-orchestration");
  const [version, setVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");
  const [strategy, setStrategy] = useState<OrchestrationStrategy>("sequential");
  const [nodes, setNodes] = useState<OrchNode[]>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [savedStatus, setSavedStatus] = useState<Orchestration["status"] | null>(null);
  const [validationErrors, setValidationErrors] = useState<OrchestrationValidationError[]>([]);
  const [savePending, setSavePending] = useState(false);
  const [deployPending, setDeployPending] = useState(false);
  const [validatePending, setValidatePending] = useState(false);

  // Load existing orchestration if ?id= provided
  useEffect(() => {
    if (!orchId) {
      setLoadedId(null);
      return;
    }
    let cancelled = false;
    api.orchestrations
      .get(orchId)
      .then((res) => {
        if (cancelled) return;
        const orch = res.data;
        setName(orch.name);
        setVersion(orch.version);
        setDescription(orch.description ?? "");
        setStrategy(orch.strategy);
        setNodes(nodesFromOrchestration(orch));
        setLoadedId(orch.id);
        setSavedStatus(orch.status);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        toast({ title: `Failed to load orchestration: ${err.message}`, variant: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [orchId, toast]);

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

  const buildCreatePayload = useCallback((): OrchestrationCreate => ({
    name,
    version,
    description,
    strategy,
    agents: buildAgentsConfig(nodes),
    layout: buildLayout(nodes),
  }), [name, version, description, strategy, nodes]);

  async function onValidate() {
    setValidatePending(true);
    setValidationErrors([]);
    try {
      const res = await api.orchestrations.validate(yamlOutput);
      const result = res.data;
      if (result.valid) {
        toast({ title: "Orchestration YAML is valid", variant: "success" });
      } else {
        setValidationErrors(result.errors);
        toast({ title: `Validation failed (${result.errors.length} error${result.errors.length === 1 ? "" : "s"})`, variant: "error" });
      }
    } catch (err) {
      toast({ title: `Validation request failed: ${(err as Error).message}`, variant: "error" });
    } finally {
      setValidatePending(false);
    }
  }

  async function onSave() {
    if (nodes.length === 0) {
      toast({ title: "Add at least one agent before saving", variant: "error" });
      return;
    }
    setSavePending(true);
    try {
      const payload = buildCreatePayload();
      const res = loadedId
        ? await api.orchestrations.update(loadedId, payload)
        : await api.orchestrations.create(payload);
      const orch = res.data;
      setLoadedId(orch.id);
      setSavedStatus(orch.status);
      toast({ title: loadedId ? "Orchestration updated" : "Orchestration saved", variant: "success" });
      if (!loadedId) navigate(`/orchestrations/builder?id=${orch.id}`, { replace: true });
    } catch (err) {
      toast({ title: `Save failed: ${(err as Error).message}`, variant: "error" });
    } finally {
      setSavePending(false);
    }
  }

  async function onDeploy() {
    if (!loadedId) {
      toast({ title: "Save the orchestration before deploying", variant: "error" });
      return;
    }
    setDeployPending(true);
    try {
      const res = await api.orchestrations.deploy(loadedId);
      setSavedStatus(res.data.status);
      toast({ title: `Deployed to ${res.data.endpoint_url ?? "endpoint"}`, variant: "success" });
    } catch (err) {
      toast({ title: `Deploy failed: ${(err as Error).message}`, variant: "error" });
    } finally {
      setDeployPending(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Orchestration Builder</h1>
          <p className="text-muted-foreground">
            Design multi-agent workflows visually
            {savedStatus && (
              <Badge variant={savedStatus === "deployed" ? "default" : "outline"} className="ml-2">
                {savedStatus}
              </Badge>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm hover:bg-muted"
            onClick={() => navigate("/orchestrations")}
          >
            <ListOrdered className="size-4" /> List
          </button>
          <button
            className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            onClick={onValidate}
            disabled={validatePending}
          >
            <CheckCircle2 className="size-4" /> {validatePending ? "Validating…" : "Validate"}
          </button>
          <button
            className="flex items-center gap-1 rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            onClick={onSave}
            disabled={savePending}
          >
            <Save className="size-4" /> {savePending ? "Saving…" : loadedId ? "Update" : "Save"}
          </button>
          <button
            className="flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
            onClick={onDeploy}
            disabled={deployPending || !loadedId}
            title={loadedId ? "Deploy this orchestration" : "Save before deploying"}
          >
            <Rocket className="size-4" /> {deployPending ? "Deploying…" : "Deploy"}
          </button>
          <button
            className={`flex items-center gap-1 rounded px-3 py-1.5 text-sm ${showYaml ? "bg-primary text-primary-foreground" : "border hover:bg-muted"}`}
            onClick={() => setShowYaml(!showYaml)}
          >
            {showYaml ? <Eye className="size-4" /> : <Code className="size-4" />}
            {showYaml ? "Canvas" : "View YAML"}
          </button>
        </div>
      </div>

      {validationErrors.length > 0 && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <div className="mb-2 flex items-center gap-2 font-medium text-destructive">
            <AlertCircle className="size-4" /> Validation errors
          </div>
          <ul className="space-y-1 text-sm">
            {validationErrors.map((e, i) => (
              <li key={i} className="text-destructive">
                <code className="font-mono text-xs">{e.path || "(root)"}</code>: {e.message}
                {e.suggestion && (
                  <span className="ml-2 text-muted-foreground">→ {e.suggestion}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

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
