import type { AgentWizardState, AgentWizardAction } from "@/lib/agent-wizard-state";

interface Props {
  state: AgentWizardState;
  dispatch: React.Dispatch<AgentWizardAction>;
}

export function Step1Goal({ state, dispatch }: Props) {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <label htmlFor="businessGoal" className="block text-sm font-medium">
          What problem does this agent solve?
        </label>
        <textarea
          id="businessGoal"
          data-testid="businessGoal"
          rows={4}
          value={state.businessGoal}
          onChange={(e) =>
            dispatch({ type: "SET_FIELD", field: "businessGoal", value: e.target.value })
          }
          placeholder="e.g. Automatically triage and respond to customer support tickets using our knowledge base"
          className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <label htmlFor="cloudPreference" className="block text-xs font-medium text-zinc-400">
            Cloud preference
          </label>
          <select
            id="cloudPreference"
            data-testid="cloudPreference"
            value={state.cloudPreference}
            onChange={(e) =>
              dispatch({
                type: "SET_FIELD",
                field: "cloudPreference",
                value: e.target.value as AgentWizardState["cloudPreference"],
              })
            }
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          >
            <option value="aws">AWS</option>
            <option value="gcp">GCP</option>
            <option value="azure">Azure</option>
            <option value="local">Local</option>
          </select>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="scaleProfile" className="block text-xs font-medium text-zinc-400">
            Scale profile
          </label>
          <select
            id="scaleProfile"
            data-testid="scaleProfile"
            value={state.scaleProfile}
            onChange={(e) =>
              dispatch({
                type: "SET_FIELD",
                field: "scaleProfile",
                value: e.target.value as AgentWizardState["scaleProfile"],
              })
            }
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          >
            <option value="realtime">Real-time</option>
            <option value="batch">Batch</option>
            <option value="event_driven">Event-driven</option>
            <option value="low_volume">Low volume</option>
          </select>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="languagePreference" className="block text-xs font-medium text-zinc-400">
            Language preference
          </label>
          <select
            id="languagePreference"
            data-testid="languagePreference"
            value={state.languagePreference}
            onChange={(e) =>
              dispatch({
                type: "SET_FIELD",
                field: "languagePreference",
                value: e.target.value as AgentWizardState["languagePreference"],
              })
            }
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          >
            <option value="python">Python</option>
            <option value="typescript">TypeScript</option>
            <option value="none">No preference</option>
          </select>
        </div>
      </div>
    </div>
  );
}
