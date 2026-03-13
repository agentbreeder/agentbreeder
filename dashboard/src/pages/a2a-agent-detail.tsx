import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import { ArrowLeft, Bot, Circle, Send, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { RelativeTime } from "@/components/ui/relative-time";

const STATUS_CONFIG: Record<string, { dotColor: string; bgColor: string; label: string }> = {
  registered: { dotColor: "text-blue-500", bgColor: "bg-blue-500/10 text-blue-600", label: "Registered" },
  active: { dotColor: "text-emerald-500", bgColor: "bg-emerald-500/10 text-emerald-600", label: "Active" },
  inactive: { dotColor: "text-muted-foreground", bgColor: "bg-muted text-muted-foreground", label: "Inactive" },
  error: { dotColor: "text-red-500", bgColor: "bg-red-500/10 text-red-600", label: "Error" },
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function TestPanel({ agentName }: { agentName: string }) {
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState<string | null>(null);

  const invokeMut = useMutation({
    mutationFn: () => api.a2a.invoke(agentName, { input_message: message }),
    onSuccess: (data) => setResponse(data.data.output),
  });

  return (
    <div className="rounded-lg border p-4">
      <h3 className="mb-3 font-medium">Test Panel</h3>
      <div className="flex gap-2">
        <Input
          className="flex-1"
          placeholder="Send a message..."
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && message && invokeMut.mutate()}
        />
        <button
          className="flex items-center gap-1 rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          disabled={!message || invokeMut.isPending}
          onClick={() => invokeMut.mutate()}
        >
          {invokeMut.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </button>
      </div>
      {response && (
        <div className="mt-3 rounded bg-muted p-3 text-sm">
          <p className="text-xs font-medium text-muted-foreground mb-1">Response</p>
          <p>{response}</p>
        </div>
      )}
      {invokeMut.isError && <p className="mt-2 text-sm text-red-500">{(invokeMut.error as Error).message}</p>}
    </div>
  );
}

export default function A2AAgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading } = useQuery({
    queryKey: ["a2a-agent", id],
    queryFn: () => api.a2a.get(id!),
    enabled: !!id,
  });

  if (isLoading) return <div className="flex justify-center py-12"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>;

  const agent = data?.data;
  if (!agent) return <div className="py-12 text-center text-muted-foreground">Agent not found</div>;

  const sc = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.inactive;
  const card = agent.agent_card as Record<string, unknown>;
  const skills = (card?.skills ?? []) as Array<{ name: string; description: string }>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to="/a2a" className="text-muted-foreground hover:text-foreground"><ArrowLeft className="size-5" /></Link>
        <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10"><Bot className="size-5 text-primary" /></div>
        <div>
          <h1 className="text-xl font-bold">{agent.name}</h1>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Circle className={cn("size-2 fill-current", sc.dotColor)} />
            <span>{sc.label}</span>
            {agent.team && <><span>·</span><span>{agent.team}</span></>}
          </div>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-4 rounded-lg border p-4">
          <h2 className="font-medium">Details</h2>
          <dl className="grid gap-3 sm:grid-cols-2">
            <Field label="Endpoint URL">{agent.endpoint_url}</Field>
            <Field label="Auth Scheme">{agent.auth_scheme ?? "none"}</Field>
            <Field label="Registered"><RelativeTime date={agent.created_at} /></Field>
            <Field label="Updated"><RelativeTime date={agent.updated_at} /></Field>
          </dl>
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Capabilities</p>
            <div className="flex flex-wrap gap-1">
              {agent.capabilities.map((c) => <Badge key={c} variant="outline">{c}</Badge>)}
              {agent.capabilities.length === 0 && <span className="text-sm text-muted-foreground">None</span>}
            </div>
          </div>
        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <h2 className="font-medium">Agent Card Skills</h2>
          {skills.length === 0 ? (
            <p className="text-sm text-muted-foreground">No skills defined in agent card</p>
          ) : (
            <div className="space-y-2">
              {skills.map((s) => (
                <div key={s.name} className="rounded border p-2">
                  <p className="font-medium text-sm">{s.name}</p>
                  <p className="text-xs text-muted-foreground">{s.description}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <TestPanel agentName={agent.name} />

      {Object.keys(card).length > 0 && (
        <div className="rounded-lg border p-4">
          <h2 className="mb-2 font-medium">Raw Agent Card</h2>
          <pre className="rounded bg-muted p-3 text-xs overflow-auto max-h-80">{JSON.stringify(card, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
