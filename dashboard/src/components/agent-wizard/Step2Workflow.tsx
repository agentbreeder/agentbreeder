import type { AgentWizardState, AgentWizardAction } from "@/lib/agent-wizard-state";

interface Props {
  state: AgentWizardState;
  dispatch: React.Dispatch<AgentWizardAction>;
}

// State flags: does the workflow need...
const STATE_FLAG_OPTIONS = [
  { code: "a", label: "Loops / retry logic" },
  { code: "b", label: "Checkpoints / resume" },
  { code: "c", label: "Human-in-the-loop approval" },
  { code: "d", label: "Parallel sub-tasks" },
] as const;

// Data flags: what data does the agent work with?
const DATA_FLAG_OPTIONS = [
  { code: "a", label: "Unstructured documents (PDFs, emails, web pages)" },
  { code: "b", label: "Structured database / SQL" },
  { code: "c", label: "Knowledge graph / relationships" },
  { code: "d", label: "Live APIs / real-time data" },
] as const;

function toggleFlag(flags: string[], code: string): string[] {
  return flags.includes(code) ? flags.filter((f) => f !== code) : [...flags, code];
}

export function Step2Workflow({ state, dispatch }: Props) {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <label htmlFor="workflow" className="block text-sm font-medium">
          Describe the workflow — one step per line
        </label>
        <textarea
          id="workflow"
          data-testid="workflow"
          rows={5}
          value={state.workflow}
          onChange={(e) =>
            dispatch({ type: "SET_FIELD", field: "workflow", value: e.target.value })
          }
          placeholder={"1. Receive incoming support ticket\n2. Classify by category\n3. Search knowledge base\n4. Draft a reply\n5. Send or escalate"}
          className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm font-mono placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>

      <div className="space-y-3">
        <p className="text-sm font-medium">Does the workflow need…</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {STATE_FLAG_OPTIONS.map(({ code, label }) => (
            <label key={code} className="flex items-center gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                data-testid={`stateFlag-${code}`}
                checked={state.stateFlags.includes(code)}
                onChange={() =>
                  dispatch({
                    type: "SET_FIELD",
                    field: "stateFlags",
                    value: toggleFlag(state.stateFlags, code),
                  })
                }
                className="h-4 w-4 rounded border-zinc-600 bg-zinc-900 text-emerald-500 focus:ring-emerald-500"
              />
              <span className="text-sm text-zinc-300">{label}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <p className="text-sm font-medium">What data does your agent work with?</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {DATA_FLAG_OPTIONS.map(({ code, label }) => (
            <label key={code} className="flex items-center gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                data-testid={`dataFlag-${code}`}
                checked={state.dataFlags.includes(code)}
                onChange={() =>
                  dispatch({
                    type: "SET_FIELD",
                    field: "dataFlags",
                    value: toggleFlag(state.dataFlags, code),
                  })
                }
                className="h-4 w-4 rounded border-zinc-600 bg-zinc-900 text-emerald-500 focus:ring-emerald-500"
              />
              <span className="text-sm text-zinc-300">{label}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
