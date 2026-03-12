import { useState, useCallback, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  ArrowLeft,
  Plus,
  Trash2,
  Play,
  AlertCircle,
  CheckCircle2,
  Code,
  List,
  Save,
  Loader2,
  Terminal,
} from "lucide-react";
import { api, type ToolDetail } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { SandboxRunner } from "@/components/sandbox-runner";
import { SubmitForReview } from "@/components/submit-for-review";

// --- Types ---

interface SchemaParameter {
  id: string;
  name: string;
  type: string;
  description: string;
  required: boolean;
  defaultValue: string;
}

interface TestRun {
  id: string;
  timestamp: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  duration: number;
}

const PARAM_TYPES = ["string", "number", "integer", "boolean", "array", "object"] as const;

const CATEGORIES = [
  { value: "search", label: "Search" },
  { value: "database", label: "Database" },
  { value: "notification", label: "Notification" },
  { value: "file", label: "File" },
  { value: "api", label: "API" },
  { value: "other", label: "Other" },
];

// --- Helpers ---

function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function parametersToSchema(params: SchemaParameter[]): Record<string, unknown> {
  const properties: Record<string, Record<string, unknown>> = {};
  const required: string[] = [];

  for (const p of params) {
    if (!p.name.trim()) continue;
    const prop: Record<string, unknown> = { type: p.type };
    if (p.description) prop.description = p.description;
    if (p.defaultValue) {
      if (p.type === "number" || p.type === "integer") {
        prop.default = Number(p.defaultValue);
      } else if (p.type === "boolean") {
        prop.default = p.defaultValue === "true";
      } else {
        prop.default = p.defaultValue;
      }
    }
    properties[p.name] = prop;
    if (p.required) required.push(p.name);
  }

  const schema: Record<string, unknown> = { type: "object", properties };
  if (required.length > 0) schema.required = required;
  return schema;
}

function schemaToParameters(schema: Record<string, unknown>): SchemaParameter[] {
  const properties = (schema.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = (schema.required ?? []) as string[];

  return Object.entries(properties).map(([name, prop]) => ({
    id: generateId(),
    name,
    type: (prop.type as string) ?? "string",
    description: (prop.description as string) ?? "",
    required: required.includes(name),
    defaultValue: prop.default !== undefined ? String(prop.default) : "",
  }));
}

function generateMockOutput(
  _schema: Record<string, unknown>,
  input: Record<string, unknown>
): Record<string, unknown> {
  const toolName = (input.query as string) || "result";
  return {
    success: true,
    data: {
      results: [
        { id: 1, name: `${toolName}-item-1`, score: 0.95 },
        { id: 2, name: `${toolName}-item-2`, score: 0.82 },
      ],
      total: 2,
      query: input,
    },
    metadata: {
      execution_time_ms: Math.floor(Math.random() * 200) + 50,
      cached: false,
    },
  };
}

// --- Components ---

function ParameterRow({
  param,
  onChange,
  onRemove,
}: {
  param: SchemaParameter;
  onChange: (updated: SchemaParameter) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-border p-3">
      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <Input
            placeholder="Field name"
            value={param.name}
            onChange={(e) => onChange({ ...param, name: e.target.value })}
            className="h-7 flex-1 font-mono text-xs"
          />
          <select
            value={param.type}
            onChange={(e) => onChange({ ...param, type: e.target.value })}
            className="h-7 rounded-md border border-input bg-background px-2 text-xs outline-none"
          >
            {PARAM_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <Input
          placeholder="Description"
          value={param.description}
          onChange={(e) => onChange({ ...param, description: e.target.value })}
          className="h-7 text-xs"
        />
        <div className="flex items-center gap-3">
          <Input
            placeholder="Default value"
            value={param.defaultValue}
            onChange={(e) => onChange({ ...param, defaultValue: e.target.value })}
            className="h-7 flex-1 text-xs"
          />
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={param.required}
              onChange={(e) => onChange({ ...param, required: e.target.checked })}
              className="rounded border-input"
            />
            Required
          </label>
        </div>
      </div>
      <button
        onClick={onRemove}
        className="mt-1 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  );
}

function FormBuilder({
  parameters,
  onParametersChange,
}: {
  parameters: SchemaParameter[];
  onParametersChange: (params: SchemaParameter[]) => void;
}) {
  const addParameter = () => {
    onParametersChange([
      ...parameters,
      {
        id: generateId(),
        name: "",
        type: "string",
        description: "",
        required: false,
        defaultValue: "",
      },
    ]);
  };

  const updateParameter = (index: number, updated: SchemaParameter) => {
    const next = [...parameters];
    next[index] = updated;
    onParametersChange(next);
  };

  const removeParameter = (index: number) => {
    onParametersChange(parameters.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-3">
      {parameters.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <List className="mb-2 size-5 text-muted-foreground/40" />
          <p className="text-xs text-muted-foreground">
            No parameters defined yet
          </p>
        </div>
      ) : (
        parameters.map((param, i) => (
          <ParameterRow
            key={param.id}
            param={param}
            onChange={(updated) => updateParameter(i, updated)}
            onRemove={() => removeParameter(i)}
          />
        ))
      )}
      <button
        onClick={addParameter}
        className="flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-border py-2 text-xs text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
      >
        <Plus className="size-3" />
        Add Parameter
      </button>
    </div>
  );
}

function JsonSchemaEditor({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | null;
}) {
  return (
    <div className="space-y-2">
      {error && (
        <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertCircle className="size-3 shrink-0" />
          {error}
        </div>
      )}
      <div className="relative">
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          className="h-80 w-full resize-none rounded-md border border-input bg-muted/30 p-3 font-mono text-xs leading-relaxed outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
        />
      </div>
    </div>
  );
}

function TestRunner({
  schema,
  parameters,
}: {
  schema: Record<string, unknown>;
  parameters: SchemaParameter[];
}) {
  const [testInputs, setTestInputs] = useState<Record<string, string>>({});
  const [testRuns, setTestRuns] = useState<TestRun[]>([]);
  const [running, setRunning] = useState(false);

  // Reset inputs when parameters change
  useEffect(() => {
    const inputs: Record<string, string> = {};
    for (const p of parameters) {
      if (p.name) inputs[p.name] = p.defaultValue || "";
    }
    setTestInputs(inputs);
  }, [parameters]);

  const runTest = useCallback(() => {
    setRunning(true);
    const input: Record<string, unknown> = {};
    for (const p of parameters) {
      if (!p.name) continue;
      const val = testInputs[p.name] ?? "";
      if (p.type === "number" || p.type === "integer") {
        input[p.name] = val ? Number(val) : 0;
      } else if (p.type === "boolean") {
        input[p.name] = val === "true";
      } else {
        input[p.name] = val;
      }
    }

    setTimeout(() => {
      const output = generateMockOutput(schema, input);
      const run: TestRun = {
        id: generateId(),
        timestamp: new Date().toISOString(),
        input,
        output,
        duration: Math.floor(Math.random() * 800) + 200,
      };
      setTestRuns((prev) => [run, ...prev].slice(0, 5));
      setRunning(false);
    }, 1000);
  }, [parameters, testInputs, schema]);

  return (
    <div className="space-y-4">
      <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        Test Input
      </h3>

      {parameters.filter((p) => p.name).length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Define parameters to generate test inputs
        </p>
      ) : (
        <div className="space-y-2">
          {parameters
            .filter((p) => p.name)
            .map((p) => (
              <div key={p.id}>
                <label className="mb-1 flex items-center gap-1 text-xs font-medium">
                  {p.name}
                  {p.required && (
                    <span className="text-destructive">*</span>
                  )}
                  <Badge variant="outline" className="ml-1 text-[9px]">
                    {p.type}
                  </Badge>
                </label>
                {p.type === "boolean" ? (
                  <select
                    value={testInputs[p.name] ?? "false"}
                    onChange={(e) =>
                      setTestInputs((prev) => ({
                        ...prev,
                        [p.name]: e.target.value,
                      }))
                    }
                    className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : (
                  <Input
                    placeholder={p.description || p.name}
                    value={testInputs[p.name] ?? ""}
                    onChange={(e) =>
                      setTestInputs((prev) => ({
                        ...prev,
                        [p.name]: e.target.value,
                      }))
                    }
                    className="h-7 text-xs"
                  />
                )}
              </div>
            ))}
        </div>
      )}

      <button
        onClick={runTest}
        disabled={running}
        className="flex w-full items-center justify-center gap-1.5 rounded-md bg-foreground px-3 py-2 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
      >
        {running ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <Play className="size-3" />
        )}
        {running ? "Running..." : "Run Tool"}
      </button>

      {/* Test history */}
      {testRuns.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Execution Log
          </h3>
          {testRuns.map((run) => (
            <div
              key={run.id}
              className="space-y-2 rounded-md border border-border p-3"
            >
              <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                <CheckCircle2 className="size-3 text-emerald-500" />
                <span>{new Date(run.timestamp).toLocaleTimeString()}</span>
                <span className="ml-auto font-mono">{run.duration}ms</span>
              </div>
              <div>
                <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                  Input
                </p>
                <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-[10px] leading-relaxed">
                  {JSON.stringify(run.input, null, 2)}
                </pre>
              </div>
              <div>
                <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                  Output
                </p>
                <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-[10px] leading-relaxed">
                  {JSON.stringify(run.output, null, 2)}
                </pre>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Main Page ---

export default function ToolBuilderPage() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isEditing = !!id;

  // Tool definition state
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("other");
  const [tagsInput, setTagsInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);

  // Schema state
  const [parameters, setParameters] = useState<SchemaParameter[]>([]);
  const [jsonText, setJsonText] = useState("{}");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"form" | "json" | "code">("form");

  // Implementation code state
  const [implementationCode, setImplementationCode] = useState(
    `"""Tool implementation — define a \`run\` function that receives validated input."""\n\ndef run(params: dict) -> dict:\n    # params contains the validated input matching your schema\n    return {"result": params}\n`
  );
  const [dependencies, setDependencies] = useState("");

  // Tool code for sandbox execution
  const [toolCode, setToolCode] = useState(
    '# Tool code — `tool_input` is a dict with your input JSON\n# Set `result` to return structured output\n\nresult = {"echo": tool_input}\n'
  );
  const [rightPanel, setRightPanel] = useState<"test" | "sandbox">("sandbox");

  // Load existing tool if editing
  const { isLoading: loadingTool } = useQuery({
    queryKey: ["tool", id],
    queryFn: () => api.tools.get(id!),
    enabled: isEditing,
    select: (resp) => resp.data,
  });

  // Populate form when tool data loads
  const { data: toolResp } = useQuery({
    queryKey: ["tool", id],
    queryFn: () => api.tools.get(id!),
    enabled: isEditing,
  });

  useEffect(() => {
    if (toolResp?.data) {
      const tool = toolResp.data as ToolDetail;
      setName(tool.name);
      setDescription(tool.description);
      if (tool.schema_definition && Object.keys(tool.schema_definition).length > 0) {
        setParameters(schemaToParameters(tool.schema_definition));
        setJsonText(JSON.stringify(tool.schema_definition, null, 2));
      }
    }
  }, [toolResp]);

  // Sync parameters -> JSON
  const handleParametersChange = useCallback(
    (params: SchemaParameter[]) => {
      setParameters(params);
      const schema = parametersToSchema(params);
      setJsonText(JSON.stringify(schema, null, 2));
      setJsonError(null);
    },
    []
  );

  // Sync JSON -> parameters
  const handleJsonChange = useCallback((text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null) {
        setJsonError("Schema must be a JSON object");
        return;
      }
      setJsonError(null);
      setParameters(schemaToParameters(parsed));
    } catch {
      setJsonError("Invalid JSON syntax");
    }
  }, []);

  // Tags handling
  const handleTagsInput = (value: string) => {
    setTagsInput(value);
    if (value.endsWith(",")) {
      const tag = value.slice(0, -1).trim();
      if (tag && !tags.includes(tag)) {
        setTags([...tags, tag]);
      }
      setTagsInput("");
    }
  };

  const removeTag = (tag: string) => {
    setTags(tags.filter((t) => t !== tag));
  };

  // Save mutation
  const saveMutation = useMutation({
    mutationFn: async () => {
      const schema = parametersToSchema(parameters);
      const parsedDeps = dependencies
        .split(",")
        .map((d) => d.trim())
        .filter(Boolean);
      const payload = {
        name,
        description,
        schema_definition: schema,
        implementation_code: implementationCode || undefined,
        dependencies: parsedDeps.length > 0 ? parsedDeps : undefined,
      };
      if (isEditing) {
        return api.tools.update(id!, payload);
      }
      return api.tools.create({
        ...payload,
        tool_type: "function",
        source: "builder",
      });
    },
    onSuccess: (resp) => {
      navigate(`/tools/${resp.data.id}`);
    },
  });

  const currentSchema = parametersToSchema(parameters);

  if (loadingTool && isEditing) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/tools")}
            className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            Tools
          </button>
          <span className="text-xs text-muted-foreground">/</span>
          <h1 className="text-sm font-semibold">
            {isEditing ? "Edit Tool" : "New Tool"}
          </h1>
        </div>
        <button
          onClick={() => saveMutation.mutate()}
          disabled={!name.trim() || saveMutation.isPending}
          className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
        >
          {saveMutation.isPending ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Save className="size-3" />
          )}
          {isEditing ? "Update Tool" : "Create Tool"}
        </button>
        <SubmitForReview
          resourceType="tool"
          resourceName={name || "untitled-tool"}
          content={JSON.stringify(parametersToSchema(parameters), null, 2)}
          variant="outline"
          className="h-7 gap-1.5 px-2 text-xs"
        />
      </div>

      {saveMutation.error && (
        <div className="mx-6 mt-3 flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertCircle className="size-3 shrink-0" />
          {(saveMutation.error as Error).message}
        </div>
      )}

      {/* 3-panel layout */}
      <div className="grid flex-1 grid-cols-[280px_1fr_300px] overflow-hidden">
        {/* Left: Tool definition */}
        <div className="overflow-y-auto border-r border-border p-4">
          <h2 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Tool Definition
          </h2>
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium">Name</label>
              <Input
                placeholder="my-search-tool"
                value={name}
                onChange={(e) =>
                  setName(
                    e.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9-_]/g, "-")
                  )
                }
                className="h-8 text-xs font-mono"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">
                Description
              </label>
              <textarea
                placeholder="What does this tool do?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-xs outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">
                Category
              </label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
              >
                {CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">Tags</label>
              <Input
                placeholder="Type and press comma..."
                value={tagsInput}
                onChange={(e) => handleTagsInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && tagsInput.trim()) {
                    e.preventDefault();
                    if (!tags.includes(tagsInput.trim())) {
                      setTags([...tags, tagsInput.trim()]);
                    }
                    setTagsInput("");
                  }
                }}
                className="h-8 text-xs"
              />
              {tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {tags.map((tag) => (
                    <Badge
                      key={tag}
                      variant="secondary"
                      className="gap-1 text-[10px]"
                    >
                      {tag}
                      <button
                        onClick={() => removeTag(tag)}
                        className="ml-0.5 text-muted-foreground hover:text-foreground"
                      >
                        x
                      </button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Center: Schema editor */}
        <div className="overflow-y-auto p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Schema Editor
            </h2>
            <div className="flex items-center gap-1 rounded-md bg-muted/40 p-0.5">
              <button
                onClick={() => setActiveTab("form")}
                className={cn(
                  "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
                  activeTab === "form"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <List className="size-3" />
                Form Builder
              </button>
              <button
                onClick={() => setActiveTab("json")}
                className={cn(
                  "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
                  activeTab === "json"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Code className="size-3" />
                JSON Schema
              </button>
              <button
                onClick={() => setActiveTab("code")}
                className={cn(
                  "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
                  activeTab === "code"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Terminal className="size-3" />
                Code
              </button>
            </div>
          </div>

          {activeTab === "form" ? (
            <FormBuilder
              parameters={parameters}
              onParametersChange={handleParametersChange}
            />
          ) : activeTab === "json" ? (
            <JsonSchemaEditor
              value={jsonText}
              onChange={handleJsonChange}
              error={jsonError}
            />
          ) : (
            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium">
                  Dependencies
                  <span className="ml-1 font-normal text-muted-foreground">
                    (comma-separated pip packages)
                  </span>
                </label>
                <Input
                  placeholder="requests, beautifulsoup4, pandas"
                  value={dependencies}
                  onChange={(e) => setDependencies(e.target.value)}
                  className="h-8 font-mono text-xs"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium">
                  Implementation
                  <span className="ml-1 font-normal text-muted-foreground">
                    (Python)
                  </span>
                </label>
                <textarea
                  value={implementationCode}
                  onChange={(e) => setImplementationCode(e.target.value)}
                  spellCheck={false}
                  className="h-[500px] w-full resize-none rounded-md border border-input bg-zinc-950 p-4 font-mono text-xs leading-relaxed text-emerald-400 outline-none placeholder:text-zinc-600 focus:border-ring focus:ring-2 focus:ring-ring/50"
                  placeholder="# Write your Python tool implementation here..."
                />
                <p className="mt-1.5 text-[10px] text-muted-foreground">
                  Define a <code className="rounded bg-muted px-1 py-0.5">run(params: dict) -&gt; dict</code> function.
                  The <code className="rounded bg-muted px-1 py-0.5">params</code> argument contains validated input matching your schema.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Right: Test runner / Sandbox */}
        <div className="overflow-y-auto border-l border-border p-4">
          {/* Panel toggle */}
          <div className="mb-4 flex items-center gap-1 rounded-md bg-muted/40 p-0.5">
            <button
              onClick={() => setRightPanel("sandbox")}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
                rightPanel === "sandbox"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Terminal className="size-3" />
              Sandbox
            </button>
            <button
              onClick={() => setRightPanel("test")}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
                rightPanel === "test"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Play className="size-3" />
              Quick Test
            </button>
          </div>

          {rightPanel === "sandbox" ? (
            <div className="space-y-4">
              <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Tool Code
              </h3>
              <textarea
                value={toolCode}
                onChange={(e) => setToolCode(e.target.value)}
                spellCheck={false}
                className="h-40 w-full resize-none rounded-md border border-input bg-muted/30 p-2.5 font-mono text-xs leading-relaxed outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
                placeholder="# Python code to execute..."
              />
              <SandboxRunner code={toolCode} />
            </div>
          ) : (
            <>
              <h2 className="mb-4 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <Play className="size-3" />
                Quick Test
              </h2>
              <TestRunner schema={currentSchema} parameters={parameters} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
