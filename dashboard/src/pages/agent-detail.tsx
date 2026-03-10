import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Circle,
  ExternalLink,
  Copy,
  Check,
  Clock,
  User,
  Users,
  Tag,
} from "lucide-react";
import { api, type Agent, type AgentStatus } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { useState } from "react";

const STATUS_MAP: Record<AgentStatus, { label: string; color: string; bg: string }> = {
  running: {
    label: "Running",
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10",
  },
  deploying: {
    label: "Deploying",
    color: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500/10",
  },
  stopped: {
    label: "Stopped",
    color: "text-muted-foreground",
    bg: "bg-muted",
  },
  failed: {
    label: "Failed",
    color: "text-destructive",
    bg: "bg-destructive/10",
  },
};

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={copy}
      className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function AgentHeader({ agent }: { agent: Agent }) {
  const s = STATUS_MAP[agent.status];
  return (
    <div className="flex items-start justify-between">
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">{agent.name}</h1>
          <span className="text-sm text-muted-foreground">v{agent.version}</span>
          <div
            className={cn(
              "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
              s.bg,
              s.color
            )}
          >
            <Circle className="size-1.5 fill-current" />
            {s.label}
          </div>
        </div>
        {agent.description && (
          <p className="max-w-2xl text-sm text-muted-foreground">{agent.description}</p>
        )}
      </div>
    </div>
  );
}

function OverviewTab({ agent }: { agent: Agent }) {
  return (
    <div className="grid gap-8 pt-6 md:grid-cols-2">
      {/* Left column */}
      <div className="space-y-6">
        <div className="rounded-lg border border-border p-4">
          <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Configuration
          </h3>
          <dl className="space-y-4">
            <Field label="Framework">
              <Badge variant="outline" className="text-xs">
                {agent.framework}
              </Badge>
            </Field>
            <Field label="Primary Model">
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {agent.model_primary}
              </code>
            </Field>
            {agent.model_fallback && (
              <Field label="Fallback Model">
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                  {agent.model_fallback}
                </code>
              </Field>
            )}
            {agent.tags.length > 0 && (
              <Field label="Tags">
                <div className="flex flex-wrap gap-1">
                  {agent.tags.map((tag) => (
                    <span
                      key={tag}
                      className="flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
                    >
                      <Tag className="size-2.5" />
                      {tag}
                    </span>
                  ))}
                </div>
              </Field>
            )}
          </dl>
        </div>
      </div>

      {/* Right column */}
      <div className="space-y-6">
        <div className="rounded-lg border border-border p-4">
          <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Metadata
          </h3>
          <dl className="space-y-4">
            <Field label="Owner">
              <span className="flex items-center gap-1.5 text-sm">
                <User className="size-3 text-muted-foreground" />
                {agent.owner}
              </span>
            </Field>
            <Field label="Team">
              <span className="flex items-center gap-1.5 text-sm">
                <Users className="size-3 text-muted-foreground" />
                {agent.team}
              </span>
            </Field>
            <Field label="Created">
              <span className="flex items-center gap-1.5 text-sm">
                <Clock className="size-3 text-muted-foreground" />
                {new Date(agent.created_at).toLocaleDateString("en-US", {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </Field>
            <Field label="Last Updated">
              <span className="flex items-center gap-1.5 text-sm">
                <Clock className="size-3 text-muted-foreground" />
                {new Date(agent.updated_at).toLocaleDateString("en-US", {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </Field>
          </dl>
        </div>

        {agent.endpoint_url && (
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Endpoint
            </h3>
            <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2">
              <code className="flex-1 truncate font-mono text-xs">
                {agent.endpoint_url}
              </code>
              <CopyButton text={agent.endpoint_url} />
              <a
                href={agent.endpoint_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
              >
                <ExternalLink className="size-3" />
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["agent", id],
    queryFn: () => api.agents.get(id!),
    enabled: !!id,
  });

  const agent = data?.data;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-6 h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="space-y-3">
          <div className="h-6 w-48 animate-pulse rounded bg-muted" />
          <div className="h-4 w-96 animate-pulse rounded bg-muted/60" />
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Link to="/agents" className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-3" /> Back to agents
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
          <p className="text-sm text-destructive">
            {error ? (error as Error).message : "Agent not found"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/agents"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Agents
      </Link>

      <AgentHeader agent={agent} />

      <Tabs defaultValue="overview" className="mt-6">
        <TabsList className="h-9 bg-transparent p-0">
          <TabsTrigger
            value="overview"
            className="rounded-none border-b-2 border-transparent px-3 py-1.5 text-xs data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
          >
            Overview
          </TabsTrigger>
          <TabsTrigger
            value="deploys"
            className="rounded-none border-b-2 border-transparent px-3 py-1.5 text-xs data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
          >
            Deploy History
          </TabsTrigger>
          <TabsTrigger
            value="logs"
            className="rounded-none border-b-2 border-transparent px-3 py-1.5 text-xs data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
          >
            Logs
          </TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewTab agent={agent} />
        </TabsContent>
        <TabsContent value="deploys">
          <div className="flex flex-col items-center py-16 text-center">
            <p className="text-sm text-muted-foreground">Deploy history coming in M4.2</p>
          </div>
        </TabsContent>
        <TabsContent value="logs">
          <div className="flex flex-col items-center py-16 text-center">
            <p className="text-sm text-muted-foreground">Live logs coming in M4.2</p>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
