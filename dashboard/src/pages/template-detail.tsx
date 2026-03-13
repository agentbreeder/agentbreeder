import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useState } from "react";
import {
  Package,
  ArrowLeft,
  Copy,
  Rocket,
  Star,
  Tag,
} from "lucide-react";

export default function TemplateDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [generatedYaml, setGeneratedYaml] = useState<string | null>(null);

  const { data: response, isLoading } = useQuery({
    queryKey: ["template", id],
    queryFn: () => api.templates.get(id!),
    enabled: !!id,
  });

  const instantiateMutation = useMutation({
    mutationFn: () => api.templates.instantiate(id!, paramValues),
    onSuccess: (res) => {
      setGeneratedYaml(res.data.yaml_content);
      queryClient.invalidateQueries({ queryKey: ["template", id] });
    },
  });

  const template = response?.data;

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="mt-4 h-64 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  if (!template) {
    return (
      <div className="p-6">
        <p className="text-muted-foreground">Template not found.</p>
      </div>
    );
  }

  const parameters = (template.parameters ?? []) as Array<{
    name: string;
    label: string;
    description: string;
    type: string;
    default: string | null;
    required: boolean;
    options: string[];
  }>;

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/templates"
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> Back to Templates
        </Link>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <Package className="size-6 text-muted-foreground" />
            <div>
              <h1 className="text-2xl font-bold">{template.name}</h1>
              <p className="text-sm text-muted-foreground">{template.description}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="rounded bg-muted px-2 py-1 font-mono text-xs">
              {template.framework}
            </span>
            <span className="text-muted-foreground">v{template.version}</span>
            <span className="flex items-center gap-1 text-muted-foreground">
              <Star className="size-3" /> {template.use_count} uses
            </span>
          </div>
        </div>

        {/* Tags */}
        {template.tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {template.tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground"
              >
                <Tag className="size-3" /> {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left: Parameters Form */}
        <div className="rounded-lg border border-border bg-card p-6">
          <h2 className="mb-4 text-lg font-semibold">Configure Parameters</h2>

          {parameters.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This template has no configurable parameters.
            </p>
          ) : (
            <div className="space-y-4">
              {parameters.map((param) => (
                <div key={param.name}>
                  <label className="mb-1 block text-sm font-medium">
                    {param.label || param.name}
                    {param.required && <span className="text-red-500"> *</span>}
                  </label>
                  {param.description && (
                    <p className="mb-1 text-xs text-muted-foreground">{param.description}</p>
                  )}
                  {param.type === "select" ? (
                    <select
                      value={paramValues[param.name] ?? param.default ?? ""}
                      onChange={(e) =>
                        setParamValues((prev) => ({ ...prev, [param.name]: e.target.value }))
                      }
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    >
                      {param.options.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={paramValues[param.name] ?? param.default ?? ""}
                      onChange={(e) =>
                        setParamValues((prev) => ({ ...prev, [param.name]: e.target.value }))
                      }
                      placeholder={param.default ?? ""}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          <button
            onClick={() => instantiateMutation.mutate()}
            disabled={instantiateMutation.isPending}
            className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <Rocket className="size-4" />
            {instantiateMutation.isPending ? "Generating..." : "Use Template"}
          </button>
        </div>

        {/* Right: Generated YAML or README */}
        <div className="rounded-lg border border-border bg-card p-6">
          {generatedYaml ? (
            <>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-lg font-semibold">Generated agent.yaml</h2>
                <button
                  onClick={() => navigator.clipboard.writeText(generatedYaml)}
                  className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                >
                  <Copy className="size-3" /> Copy
                </button>
              </div>
              <pre className="max-h-[600px] overflow-auto rounded-md bg-muted p-4 font-mono text-xs">
                {generatedYaml}
              </pre>
            </>
          ) : (
            <>
              <h2 className="mb-3 text-lg font-semibold">README</h2>
              <div className="prose prose-sm dark:prose-invert max-w-none">
                {template.readme ? (
                  <pre className="whitespace-pre-wrap text-sm text-muted-foreground">
                    {template.readme}
                  </pre>
                ) : (
                  <p className="text-sm text-muted-foreground">No README provided.</p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
