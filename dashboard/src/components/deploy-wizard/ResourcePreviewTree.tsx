import type { Action, DeployWizardState } from "@/lib/deploy-wizard-state";
import { estimateMonthly } from "@/lib/deploy-wizard-cost";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

export function ResourcePreviewTree({ state, dispatch }: Props) {
  const cost =
    state.cloud && state.region
      ? estimateMonthly(state.cloud, state.region, {
          hasMemory: !!state.agentSnapshot?.declaresMemory,
          isPublic: false,
        })
      : null;

  return (
    <div className="space-y-3 border border-zinc-800 rounded p-3 text-sm">
      <h3 className="font-medium">AgentBreeder will create:</h3>
      <ul className="text-xs text-zinc-300 space-y-1 list-disc list-inside">
        {cost?.lines.map((l) => (
          <li key={l.resource}>
            + {l.resource}{" "}
            <span className="text-zinc-500">(~${l.usd}/mo)</span>
          </li>
        ))}
      </ul>

      {cost?.status !== "unsupported" && cost && (
        <div className="text-xs text-zinc-400">
          Estimated total:{" "}
          <span className="font-mono text-zinc-200">
            ${cost.low}–${cost.high}/mo
          </span>
        </div>
      )}

      <label className="flex items-start gap-2 text-xs pt-2 border-t border-zinc-800">
        <input
          type="checkbox"
          checked={state.provisionAck}
          onChange={() => {
            if (!state.provisionAck) dispatch({ type: "ACK_PROVISION" });
          }}
          className="mt-0.5"
        />
        <span>
          I understand this creates cloud resources billable to my{" "}
          {state.cloud?.toUpperCase()} account.
        </span>
      </label>
    </div>
  );
}
