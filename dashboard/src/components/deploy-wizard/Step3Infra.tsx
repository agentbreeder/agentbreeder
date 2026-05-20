import type { Action, DeployWizardState } from "@/lib/deploy-wizard-state";
import { InfraValidatePanel } from "@/components/deploy-wizard/InfraValidatePanel";
import { ResourcePreviewTree } from "@/components/deploy-wizard/ResourcePreviewTree";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

export function Step3Infra({ state, dispatch }: Props) {
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Step 3 — Infrastructure mode</h2>

      <div className="space-y-2">
        <label className="flex items-start gap-3 p-3 border border-zinc-800 rounded cursor-pointer hover:border-zinc-700">
          <input
            type="radio"
            name="infra-mode"
            checked={state.infraMode === "byo"}
            onChange={() => dispatch({ type: "SET_INFRA_MODE", mode: "byo" })}
            className="mt-1"
          />
          <span>
            <span className="block font-medium">Bring Your Own Infrastructure</span>
            <span className="block text-xs text-zinc-400">
              Validate that existing cloud resources are reachable from this team.
            </span>
          </span>
        </label>

        <label className="flex items-start gap-3 p-3 border border-zinc-800 rounded cursor-pointer hover:border-zinc-700">
          <input
            type="radio"
            name="infra-mode"
            checked={state.infraMode === "provision"}
            onChange={() =>
              dispatch({ type: "SET_INFRA_MODE", mode: "provision" })
            }
            className="mt-1"
          />
          <span>
            <span className="block font-medium">
              Provision for me{" "}
              <span className="text-xs text-emerald-400 ml-1">BETA</span>
            </span>
            <span className="block text-xs text-zinc-400">
              AgentBreeder creates VPC, IAM, container registry, and (if needed)
              a managed database for you.
            </span>
          </span>
        </label>
      </div>

      {state.infraMode === "byo" && (
        <InfraValidatePanel state={state} dispatch={dispatch} />
      )}
      {state.infraMode === "provision" && (
        <ResourcePreviewTree state={state} dispatch={dispatch} />
      )}
    </div>
  );
}
