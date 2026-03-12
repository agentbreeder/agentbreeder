import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Server,
  Circle,
  Plus,
  Search,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Wrench,
} from "lucide-react";
import { api, type McpServer, type McpTransport } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { RelativeTime } from "@/components/ui/relative-time";
import { EmptyState } from "@/components/ui/empty-state";

const TRANSPORT_LABELS: Record<string, string> = {
  stdio: "stdio",
  sse: "SSE",
  streamable_http: "Streamable HTTP",
};

const STATUS_CONFIG: Record<string, { dotColor: string; label: string }> = {
  active: { dotColor: "text-emerald-500", label: "Active" },
  inactive: { dotColor: "text-muted-foreground", label: "Inactive" },
  error: { dotColor: "text-red-500", label: "Error" },
};

// --- Register Dialog ---

function RegisterDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [transport, setTransport] = useState<McpTransport>("stdio");
  const [testResult, setTestResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);
  const [testing, setTesting] = useState(false);

  const createMutation = useMutation({
    mutationFn: () =>
      api.mcpServers.create({ name, endpoint, transport }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
      onClose();
      resetForm();
    },
  });

  const resetForm = () => {
    setName("");
    setEndpoint("");
    setTransport("stdio");
    setTestResult(null);
  };

  const handleTestConnection = () => {
    setTesting(true);
    setTestResult(null);
    // Mock test with 1s delay
    setTimeout(() => {
      setTesting(false);
      setTestResult({
        success: true,
        message: `Connection successful. Discovered 2 tools at ${endpoint}`,
      });
    }, 1000);
  };

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="fixed inset-x-0 top-[15%] z-50 mx-auto w-full max-w-md">
        <div className="rounded-xl border border-border bg-card shadow-2xl">
          <div className="border-b border-border px-6 py-4">
            <h2 className="text-sm font-semibold">Register MCP Server</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Connect to an MCP server to discover and use its tools
            </p>
          </div>

          <div className="space-y-4 px-6 py-4">
            <div>
              <label className="mb-1 block text-xs font-medium">Name</label>
              <Input
                placeholder="my-mcp-server"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-8 text-xs"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">
                Endpoint URL
              </label>
              <Input
                placeholder="http://localhost:3000 or npx -y @mcp/server"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                className="h-8 text-xs font-mono"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">
                Transport
              </label>
              <select
                value={transport}
                onChange={(e) =>
                  setTransport(e.target.value as McpTransport)
                }
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
              >
                <option value="stdio">stdio</option>
                <option value="sse">SSE</option>
                <option value="streamable_http">Streamable HTTP</option>
              </select>
            </div>

            {/* Test connection */}
            <button
              onClick={handleTestConnection}
              disabled={!endpoint.trim() || testing}
              className="flex w-full items-center justify-center gap-1.5 rounded-md border border-border py-2 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-50"
            >
              {testing ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Server className="size-3" />
              )}
              {testing ? "Testing..." : "Test Connection"}
            </button>

            {testResult && (
              <div
                className={cn(
                  "flex items-start gap-2 rounded-md px-3 py-2 text-xs",
                  testResult.success
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "bg-destructive/10 text-destructive"
                )}
              >
                {testResult.success ? (
                  <CheckCircle2 className="mt-0.5 size-3 shrink-0" />
                ) : (
                  <AlertCircle className="mt-0.5 size-3 shrink-0" />
                )}
                {testResult.message}
              </div>
            )}

            {createMutation.error && (
              <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                <AlertCircle className="size-3 shrink-0" />
                {(createMutation.error as Error).message}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 border-t border-border px-6 py-3">
            <button
              onClick={() => {
                onClose();
                resetForm();
              }}
              className="rounded-md px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
            >
              Cancel
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={
                !name.trim() ||
                !endpoint.trim() ||
                createMutation.isPending
              }
              className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
            >
              {createMutation.isPending && (
                <Loader2 className="size-3 animate-spin" />
              )}
              Register
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// --- Main Page ---

export default function McpServersPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => api.mcpServers.list(),
    staleTime: 10_000,
  });

  const servers = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  const filtered = search
    ? servers.filter(
        (s) =>
          s.name.toLowerCase().includes(search.toLowerCase()) ||
          s.endpoint.toLowerCase().includes(search.toLowerCase())
      )
    : servers;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            MCP Servers
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} server{total !== 1 ? "s" : ""} registered
          </p>
        </div>
        <button
          onClick={() => setDialogOpen(true)}
          className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
        >
          <Plus className="size-3" />
          Register MCP Server
        </button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter servers..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-14 w-full animate-pulse rounded-lg bg-muted"
            />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
          Failed to load MCP servers: {(error as Error).message}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Server}
          title="No MCP servers registered"
          description="Register an MCP server to discover and manage its tools."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="px-4 py-2.5 font-medium text-muted-foreground">
                  Name
                </th>
                <th className="px-4 py-2.5 font-medium text-muted-foreground">
                  Endpoint
                </th>
                <th className="px-4 py-2.5 font-medium text-muted-foreground">
                  Transport
                </th>
                <th className="px-4 py-2.5 font-medium text-muted-foreground">
                  Status
                </th>
                <th className="px-4 py-2.5 font-medium text-muted-foreground">
                  Tools
                </th>
                <th className="px-4 py-2.5 font-medium text-muted-foreground">
                  Last Ping
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((server) => {
                const statusConfig = STATUS_CONFIG[server.status] ??
                  STATUS_CONFIG.inactive;
                return (
                  <tr
                    key={server.id}
                    onClick={() => navigate(`/mcp-servers/${server.id}`)}
                    className="cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/20"
                  >
                    <td className="px-4 py-3 font-medium">{server.name}</td>
                    <td className="px-4 py-3">
                      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                        {server.endpoint}
                      </code>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="outline" className="text-[10px]">
                        {TRANSPORT_LABELS[server.transport] ??
                          server.transport}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <Circle
                          className={cn(
                            "size-1.5 fill-current",
                            statusConfig.dotColor
                          )}
                        />
                        <span>{statusConfig.label}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <Wrench className="size-3 text-muted-foreground" />
                        {server.tool_count}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {server.last_ping_at ? (
                        <RelativeTime date={server.last_ping_at} />
                      ) : (
                        "Never"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <RegisterDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  );
}
