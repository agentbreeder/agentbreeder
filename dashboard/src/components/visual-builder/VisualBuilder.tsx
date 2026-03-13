/**
 * VisualBuilder — main ReactFlow canvas with drag-and-drop nodes.
 * Provides the visual agent composition experience (M12.2).
 */

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import {
  ReactFlow,
  Controls,
  Background,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node,
  type Edge,
  type NodeTypes,
  BackgroundVariant,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  AgentNode,
  ModelNode,
  ToolNode,
  McpServerNode,
  PromptNode,
  MemoryNode,
  RagNode,
  GuardrailNode,
} from "./nodes";
import { NodePalette } from "./NodePalette";
import { PropertyPanel } from "./PropertyPanel";
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
} from "./types";

// Register custom node types
const nodeTypes: NodeTypes = {
  agent: AgentNode,
  model: ModelNode,
  tool: ToolNode,
  mcpServer: McpServerNode,
  prompt: PromptNode,
  memory: MemoryNode,
  rag: RagNode,
  guardrail: GuardrailNode,
};

/** Default data factories for each node type. */
function createDefaultData(type: CanvasNodeType): CanvasNodeData {
  switch (type) {
    case "agent":
      return {
        type: "agent",
        name: "my-agent",
        version: "0.1.0",
        description: "",
        team: "engineering",
        owner: "user@example.com",
        framework: "langgraph",
        tags: [],
        cloud: "local",
        runtime: "docker-compose",
        scalingMin: 1,
        scalingMax: 10,
      } satisfies AgentNodeData;
    case "model":
      return {
        type: "model",
        name: "claude-sonnet-4",
        provider: "anthropic",
        temperature: 0.7,
        maxTokens: 4096,
        role: "primary",
      } satisfies ModelNodeData;
    case "tool":
      return {
        type: "tool",
        ref: "",
        name: "my-tool",
        toolType: "function",
      } satisfies ToolNodeData;
    case "mcpServer":
      return {
        type: "mcpServer",
        name: "my-server",
        endpoint: "http://localhost:3000",
        transport: "stdio",
      } satisfies McpServerNodeData;
    case "prompt":
      return {
        type: "prompt",
        ref: "",
        name: "system-prompt",
        content: "",
        role: "system",
      } satisfies PromptNodeData;
    case "memory":
      return {
        type: "memory",
        name: "conversation-memory",
        backendType: "redis",
        memoryType: "conversation",
        maxMessages: 100,
      } satisfies MemoryNodeData;
    case "rag":
      return {
        type: "rag",
        ref: "",
        name: "knowledge-base",
        embeddingModel: "text-embedding-3-small",
      } satisfies RagNodeData;
    case "guardrail":
      return {
        type: "guardrail",
        name: "PII Detection",
        guardrailType: "pii_detection",
      } satisfies GuardrailNodeData;
  }
}

let nodeIdCounter = 0;
function nextNodeId(type: string): string {
  return `${type}-${++nodeIdCounter}`;
}

// ---------------------------------------------------------------------------
// MiniMap color
// ---------------------------------------------------------------------------
function miniMapNodeColor(node: Node<CanvasNodeData>): string {
  const colorMap: Record<CanvasNodeType, string> = {
    agent: "#3b82f6",
    model: "#8b5cf6",
    tool: "#22c55e",
    mcpServer: "#14b8a6",
    prompt: "#f59e0b",
    memory: "#ec4899",
    rag: "#6366f1",
    guardrail: "#ef4444",
  };
  return colorMap[node.data.type as CanvasNodeType] ?? "#888";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface VisualBuilderProps {
  initialNodes?: Node<CanvasNodeData>[];
  initialEdges?: Edge[];
  onNodesChange?: (nodes: Node<CanvasNodeData>[]) => void;
  onEdgesChange?: (edges: Edge[]) => void;
}

export function VisualBuilder({
  initialNodes = [],
  initialEdges = [],
  onNodesChange: onNodesChangeExternal,
  onEdgesChange: onEdgesChangeExternal,
}: VisualBuilderProps) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance<Node<CanvasNodeData>, Edge> | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<CanvasNodeData>>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  // Keep parent synced via refs so callbacks always see latest values
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  });

  // Notify parent when nodes/edges change
  const handleNodesChangeWrapped = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      onNodesChange(changes);
      // The state update is async, so we schedule notification
      requestAnimationFrame(() => {
        onNodesChangeExternal?.(nodesRef.current);
      });
    },
    [onNodesChange, onNodesChangeExternal]
  );

  const handleEdgesChangeWrapped = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      onEdgesChange(changes);
      requestAnimationFrame(() => {
        onEdgesChangeExternal?.(edgesRef.current);
      });
    },
    [onEdgesChange, onEdgesChangeExternal]
  );

  // Handle new connections
  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => {
        const newEdges = addEdge({ ...connection, type: "smoothstep" }, eds);
        requestAnimationFrame(() => {
          onEdgesChangeExternal?.(newEdges);
        });
        return newEdges;
      });
    },
    [setEdges, onEdgesChangeExternal]
  );

  // Handle drop from palette
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const type = event.dataTransfer.getData("application/reactflow") as CanvasNodeType;
      if (!type || !reactFlowInstance || !reactFlowWrapper.current) return;

      const bounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = reactFlowInstance.screenToFlowPosition({
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });

      const newNode: Node<CanvasNodeData> = {
        id: nextNodeId(type),
        type,
        position,
        data: createDefaultData(type),
      };

      setNodes((nds) => {
        const updated = [...nds, newNode];
        requestAnimationFrame(() => {
          onNodesChangeExternal?.(updated);
        });
        return updated;
      });
    },
    [reactFlowInstance, setNodes, onNodesChangeExternal]
  );

  // Node selection
  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node<CanvasNodeData>) => {
      setSelectedNodeId(node.id);
    },
    []
  );

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
  }, []);

  // Update node data from PropertyPanel
  const handleUpdateNode = useCallback(
    (id: string, dataPatch: Partial<CanvasNodeData>) => {
      setNodes((nds) => {
        const updated = nds.map((n) =>
          n.id === id
            ? { ...n, data: { ...n.data, ...dataPatch } as CanvasNodeData }
            : n
        ) as Node<CanvasNodeData>[];
        requestAnimationFrame(() => {
          onNodesChangeExternal?.(updated);
        });
        return updated;
      });
    },
    [setNodes, onNodesChangeExternal]
  );

  // Delete node
  const handleDeleteNode = useCallback(
    (id: string) => {
      setNodes((nds) => {
        const updated = nds.filter((n) => n.id !== id);
        requestAnimationFrame(() => {
          onNodesChangeExternal?.(updated);
        });
        return updated;
      });
      setEdges((eds) => {
        const updated = eds.filter((e) => e.source !== id && e.target !== id);
        requestAnimationFrame(() => {
          onEdgesChangeExternal?.(updated);
        });
        return updated;
      });
      setSelectedNodeId(null);
    },
    [setNodes, setEdges, onNodesChangeExternal, onEdgesChangeExternal]
  );

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId]
  );

  // Provide a way to externally set nodes/edges (for YAML sync)
  // This is exposed via the component's ref-like pattern through props
  // The parent should pass new initialNodes/initialEdges when switching tabs

  return (
    <div className="flex h-full">
      {/* Left: Node Palette */}
      <aside className="w-56 shrink-0 border-r border-border bg-background">
        <NodePalette />
      </aside>

      {/* Center: ReactFlow Canvas */}
      <div className="relative flex-1" ref={reactFlowWrapper}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChangeWrapped}
          onEdgesChange={handleEdgesChangeWrapped}
          onConnect={onConnect}
          onInit={setReactFlowInstance}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          deleteKeyCode={["Backspace", "Delete"]}
          className="bg-background"
          proOptions={{ hideAttribution: true }}
        >
          <Controls className="!rounded-lg !border !border-border !bg-card !shadow-md [&>button]:!border-border [&>button]:!bg-card [&>button]:!text-foreground [&>button:hover]:!bg-muted" />
          <Background
            variant={BackgroundVariant.Dots}
            gap={20}
            size={1}
            className="!bg-background"
          />
          <MiniMap
            nodeColor={miniMapNodeColor}
            className="!rounded-lg !border !border-border !bg-card !shadow-md"
            maskColor="rgb(0, 0, 0, 0.05)"
          />
        </ReactFlow>

        {/* Empty state hint */}
        {nodes.length === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div className="rounded-xl border border-dashed border-border/60 bg-card/80 px-8 py-6 text-center shadow-sm backdrop-blur-sm">
              <div className="text-sm font-medium text-foreground/60">
                Drag nodes from the palette to get started
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Start with an Agent node, then connect Models, Tools, and more
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right: Property Panel */}
      <aside className="w-64 shrink-0 border-l border-border bg-background">
        <PropertyPanel
          node={selectedNode}
          onUpdateNode={handleUpdateNode}
          onDeleteNode={handleDeleteNode}
          onClose={() => setSelectedNodeId(null)}
        />
      </aside>
    </div>
  );
}

// Re-export for external use
export { createDefaultData, nextNodeId };
