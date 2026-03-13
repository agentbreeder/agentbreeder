import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bot, Circle, Plus, Search, Loader2, Network } from "lucide-react";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { RelativeTime } from "@/components/ui/relative-time";
import { EmptyState } from "@/components/ui/empty-state";

const STATUS_CONFIG: Record<string, { dotColor: string; label: string }> = {
  registered: { dotColor: "text-blue-500", label: "Registered" },
  active: { dotColor: "text-emerald-500", label: "Active" },
  inactive: { dotColor: "text-muted-foreground", label: "Inactive" },
  error: { dotColor: "text-red-500", label: "Error" },
};

function RegisterDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [team, setTeam] = useState("");

  const createMut = useMutation({
    mutationFn: () => api.a2a.create({ name, endpoint_url: endpointUrl, team: team || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["a2a-agents"] });
      onClose();
      setName("");
      setEndpointUrl("");
      setTeam("");
    },
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg border bg-background p-6 shadow-lg">
        <h2 className="mb-4 text-lg font-semibold">Register A2A Agent</h2>
        <div className="space-y-3">
          <Input placeholder="Agent name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input placeholder="Endpoint URL" value={endpointUrl} onChange={(e) => setEndpointUrl(e.target.value)} />
          <Input placeholder="Team (optional)" value={team} onChange={(e) => setTeam(e.target.value)} />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="rounded px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted" onClick={onClose}>Cancel</button>
          <button
            className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
            disabled={!name || !endpointUrl || createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending ? <Loader2 className="size-4 animate-spin" /> : "Register"}
          </button>
        </div>
        {createMut.isError && <p className="mt-2 text-sm text-red-500">{(createMut.error as Error).message}</p>}
      </div>
    </div>
  );
}

export default function A2AAgentsPage() {
  const [search, setSearch] = useState("");
  const [showRegister, setShowRegister] = useState(false);
  const navigate = useNavigate();

  const { data, isLoading } = useQuery({
    queryKey: ["a2a-agents"],
    queryFn: () => api.a2a.list(),
  });

  const agents = data?.data ?? [];
  const filtered = agents.filter(
    (a) =>
      a.name.toLowerCase().includes(search.toLowerCase()) ||
      (a.team ?? "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">A2A Agents</h1>
          <p className="text-muted-foreground">Agent-to-Agent communication registry</p>
        </div>
        <button
          className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          onClick={() => setShowRegister(true)}
        >
          <Plus className="size-4" /> Register Agent
        </button>
      </div>

      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-9" placeholder="Search agents..." value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>
      ) : filtered.length === 0 ? (
        <EmptyState icon={Network} title="No A2A agents" description="Register an agent to enable inter-agent communication." />
      ) : (
        <div className="grid gap-3">
          {filtered.map((agent) => {
            const sc = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.inactive;
            return (
              <button
                key={agent.id}
                className="flex items-center gap-4 rounded-lg border bg-card p-4 text-left transition-colors hover:bg-muted/50"
                onClick={() => navigate(`/a2a/${agent.id}`)}
              >
                <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                  <Bot className="size-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{agent.name}</span>
                    <Circle className={cn("size-2 fill-current", sc.dotColor)} />
                    <span className="text-xs text-muted-foreground">{sc.label}</span>
                  </div>
                  <div className="text-sm text-muted-foreground truncate">{agent.endpoint_url}</div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  {agent.team && <Badge variant="secondary">{agent.team}</Badge>}
                  <span className="text-xs text-muted-foreground">
                    <RelativeTime date={agent.created_at} />
                  </span>
                </div>
                <div className="flex gap-1">
                  {agent.capabilities.slice(0, 3).map((c) => (
                    <Badge key={c} variant="outline" className="text-xs">{c}</Badge>
                  ))}
                </div>
              </button>
            );
          })}
        </div>
      )}

      <RegisterDialog open={showRegister} onClose={() => setShowRegister(false)} />
    </div>
  );
}
