import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Action,
  AgentSnapshot,
  DeployWizardState,
} from "@/lib/deploy-wizard-state";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

export function Step1Agent({ state, dispatch }: Props) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["deploy-wizard", "agents"],
    queryFn: () => api.agents.list(),
  });

  if (isLoading) return <p className="text-zinc-400">Loading agents…</p>;
  if (error)
    return (
      <div className="text-red-400 space-y-2">
        <p>Couldn't load agents.</p>
        <button
          type="button"
          onClick={() => refetch()}
          className="underline text-sm"
        >
          Retry
        </button>
      </div>
    );

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-medium">Step 1 — Select an agent</h2>
      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {data?.data?.map((a) => {
          const snapshot: AgentSnapshot = {
            id: a.id,
            name: a.name,
            framework: a.framework,
            version: a.version,
            team: a.team,
            requiresApproval: Boolean(
              (a as unknown as { access?: { require_approval?: boolean } }).access
                ?.require_approval ?? a.config_snapshot?.requiresApproval,
            ),
            declaresMemory: Boolean(
              (a as unknown as { memory?: unknown }).memory ?? a.config_snapshot?.declaresMemory,
            ),
          };
          const selected = state.agentId === a.id;
          return (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => dispatch({ type: "SET_AGENT", agent: snapshot })}
                className={`block w-full text-left p-3 border rounded transition-colors ${
                  selected
                    ? "border-emerald-500 bg-emerald-500/10"
                    : "border-zinc-800 hover:border-zinc-700"
                }`}
              >
                <div className="font-medium">{a.name}</div>
                <div className="text-xs text-zinc-400">
                  {a.framework} v{a.version} · {a.team}
                  {snapshot.requiresApproval && " · approval required"}
                  {snapshot.declaresMemory && " · memory"}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
