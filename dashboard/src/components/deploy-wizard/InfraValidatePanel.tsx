import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Action, DeployWizardState } from "@/lib/deploy-wizard-state";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

export function InfraValidatePanel({ state, dispatch }: Props) {
  const cloud = state.cloud!;
  const { data: requirementsResp, isLoading } = useQuery({
    queryKey: ["deploy-wizard", "cloud-requirements", cloud],
    queryFn: () => api.deployments.cloudRequirements(cloud, "simple"),
    enabled: !!cloud,
  });

  const validate = useMutation({
    mutationFn: () =>
      api.deployments.validateInfra({
        cloud,
        region: state.region!,
        team_id: state.agentSnapshot!.team,
        mode: "simple",
        fields: state.byoFields,
      }),
    onSuccess: (resp) => {
      dispatch({ type: "SET_VALIDATION", result: resp.data });
    },
  });

  if (isLoading)
    return <p className="text-zinc-400">Loading required fields…</p>;
  const fields = requirementsResp?.data?.fields ?? [];

  return (
    <div className="space-y-3 border border-zinc-800 rounded p-3">
      <h3 className="font-medium text-sm">BYO infrastructure fields</h3>
      <div className="space-y-2">
        {fields.map((f) => (
          <label key={f.name} className="block text-xs">
            <span className="block text-zinc-400">
              {f.name} {f.required && <span className="text-red-400">*</span>}
              <span className="text-zinc-600 ml-1">{f.description}</span>
            </span>
            <input
              type="text"
              className="mt-1 w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
              value={state.byoFields[f.name] ?? ""}
              onChange={(e) =>
                dispatch({
                  type: "SET_BYO_FIELD",
                  key: f.name,
                  value: e.target.value,
                })
              }
            />
          </label>
        ))}
      </div>

      <button
        type="button"
        onClick={() => validate.mutate()}
        disabled={validate.isPending}
        className="px-3 py-1.5 text-sm border border-zinc-700 rounded hover:border-emerald-500 disabled:opacity-50"
      >
        {validate.isPending ? "Validating…" : "Validate infrastructure"}
      </button>

      {state.validateResult && (
        <ul className="space-y-1 text-xs">
          {state.validateResult.checks.map((c) => (
            <li
              key={c.resource}
              className={
                c.status === "found" ? "text-emerald-400" : "text-red-400"
              }
            >
              {c.status === "found" ? "✓" : "✗"} {c.resource} — {c.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
