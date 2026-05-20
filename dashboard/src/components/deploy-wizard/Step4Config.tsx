import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Action,
  DeployWizardState,
  EnvVar,
} from "@/lib/deploy-wizard-state";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

const DB_TIERS = ["db-f1-micro", "db-g1-small", "db-n1-standard-1"] as const;

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export function Step4Config({ state, dispatch }: Props) {
  const requiresApproval = !!state.agentSnapshot?.requiresApproval;
  const declaresMemory = !!state.agentSnapshot?.declaresMemory;

  const submit = useMutation({
    mutationFn: async () => {
      let key = state.idempotencyKey;
      if (!key) {
        key = newIdempotencyKey();
        dispatch({ type: "SET_IDEMPOTENCY_KEY", key });
      }
      return api.deployments.createJob(
        {
          agent_id: state.agentId!,
          cloud: state.cloud!,
          region: state.region!,
          infra_mode: state.infraMode!,
          byo_fields: state.byoFields,
          env_vars: state.envVars,
          secrets: state.secrets,
          scaling: {
            min: state.scaling.min,
            max: state.scaling.max,
            cpu_target_pct: state.scaling.cpuTargetPct,
          },
          db_tier: state.dbTier,
        },
        key,
      );
    },
    onSuccess: (resp) => {
      dispatch({
        type: "SUBMIT_DEPLOY",
        jobId: resp.data.job_id,
        pendingApproval: resp.data.pending_approval,
      });
    },
  });

  function addEnvVar(): void {
    const key = `KEY_${state.envVars.length + 1}`;
    dispatch({ type: "SET_ENV_VAR", key, value: "" });
  }

  function updateEnvVar(env: EnvVar, field: "key" | "value", v: string): void {
    if (field === "key") {
      // Rename: remove old, add new.
      dispatch({ type: "REMOVE_ENV_VAR", key: env.key });
      dispatch({ type: "SET_ENV_VAR", key: v, value: env.value });
    } else {
      dispatch({ type: "SET_ENV_VAR", key: env.key, value: v });
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Step 4 — Configuration</h2>

      <section>
        <h3 className="text-sm font-medium mb-2">Environment variables</h3>
        <div className="space-y-1.5">
          {state.envVars.map((env) => (
            <div key={env.key} className="flex gap-2 items-center text-sm">
              <input
                aria-label={`env-${env.key}-name`}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 w-1/3"
                value={env.key}
                onChange={(e) => updateEnvVar(env, "key", e.target.value)}
              />
              <input
                aria-label={`env-${env.key}-value`}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 flex-1"
                value={env.value}
                onChange={(e) => updateEnvVar(env, "value", e.target.value)}
              />
              <button
                type="button"
                onClick={() => dispatch({ type: "REMOVE_ENV_VAR", key: env.key })}
                className="text-xs text-zinc-500 hover:text-red-400"
              >
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addEnvVar}
            className="text-xs text-emerald-400 hover:underline"
          >
            + Add env var
          </button>
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Scaling</h3>
        <div className="grid grid-cols-3 gap-3 text-sm">
          <label>
            <span className="block text-xs text-zinc-400">Min instances</span>
            <input
              type="number"
              min={0}
              value={state.scaling.min}
              onChange={(e) =>
                dispatch({
                  type: "SET_SCALING",
                  scaling: { ...state.scaling, min: Number(e.target.value) },
                })
              }
              className="mt-1 w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1"
            />
          </label>
          <label>
            <span className="block text-xs text-zinc-400">Max instances</span>
            <input
              type="number"
              min={1}
              value={state.scaling.max}
              onChange={(e) =>
                dispatch({
                  type: "SET_SCALING",
                  scaling: { ...state.scaling, max: Number(e.target.value) },
                })
              }
              className="mt-1 w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1"
            />
          </label>
          <label>
            <span className="block text-xs text-zinc-400">CPU target %</span>
            <input
              type="number"
              min={10}
              max={100}
              value={state.scaling.cpuTargetPct}
              onChange={(e) =>
                dispatch({
                  type: "SET_SCALING",
                  scaling: { ...state.scaling, cpuTargetPct: Number(e.target.value) },
                })
              }
              className="mt-1 w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1"
            />
          </label>
        </div>
      </section>

      {declaresMemory && (
        <section>
          <label className="text-sm">
            <span className="block text-xs text-zinc-400 mb-1">DB tier</span>
            <select
              aria-label="DB tier"
              className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
              value={state.dbTier ?? ""}
              onChange={(e) =>
                e.target.value &&
                dispatch({ type: "SET_DB_TIER", tier: e.target.value })
              }
            >
              <option value="">
                Default (
                {state.cloud === "aws"
                  ? "t3.micro"
                  : state.cloud === "gcp"
                    ? "db-f1-micro"
                    : "B1ms"}
                )
              </option>
              {DB_TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        </section>
      )}

      <div className="pt-2 border-t border-zinc-800 flex justify-end">
        <button
          type="button"
          onClick={() => submit.mutate()}
          disabled={submit.isPending}
          className="px-4 py-1.5 bg-emerald-500 text-black rounded font-medium disabled:opacity-50"
        >
          {submit.isPending
            ? "Submitting…"
            : requiresApproval
              ? "Submit for approval"
              : "Deploy"}
        </button>
      </div>
    </div>
  );
}
