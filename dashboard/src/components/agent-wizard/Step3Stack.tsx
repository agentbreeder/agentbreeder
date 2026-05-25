import type { AgentWizardState, AgentWizardAction } from "@/lib/agent-wizard-state";
import { deployTargetToCloud } from "@/lib/agent-wizard-state";
import { useMutation } from "@tanstack/react-query";
import { api, type RecommendInput } from "@/lib/api";
import { useEffect } from "react";

interface Props {
  state: AgentWizardState;
  dispatch: React.Dispatch<AgentWizardAction>;
}

const FRAMEWORK_OPTIONS = [
  "langgraph",
  "crewai",
  "claude_sdk",
  "openai_agents",
  "google_adk",
  "custom",
] as const;

const CLOUD_OPTIONS = [
  { value: "aws", label: "AWS" },
  { value: "gcp", label: "GCP" },
  { value: "azure", label: "Azure" },
  { value: "local", label: "Local" },
] as const;

export function Step3Stack({ state, dispatch }: Props) {
  const mutation = useMutation({
    mutationFn: async () => {
      const input: RecommendInput = {
        business_goal: state.businessGoal,
        technical_use_case: state.workflow,
        state_flags: state.stateFlags,
        cloud_preference: state.cloudPreference,
        language_preference: state.languagePreference,
        data_flags: state.dataFlags,
        scale_profile: state.scaleProfile,
      };
      const res = await api.builders.recommend(input);
      return res.data;
    },
    onSuccess: (rec) => {
      dispatch({ type: "SET_RECOMMENDATION", recommendation: rec });
    },
  });

  // Fire the recommendation when we first enter step 3 (if not already done)
  useEffect(() => {
    if (state.recommendation === null && !mutation.isPending && !mutation.isSuccess) {
      mutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rec = state.recommendation;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">Recommended stack</h2>
        <button
          type="button"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
          className="text-xs text-emerald-400 hover:text-emerald-300 disabled:opacity-50"
        >
          {mutation.isPending ? "Analyzing…" : "Re-analyze"}
        </button>
      </div>

      {mutation.isError && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          Recommendation failed — you can edit the fields below manually or{" "}
          <button
            type="button"
            onClick={() => mutation.mutate()}
            className="underline hover:text-red-300"
          >
            retry
          </button>
          .
        </div>
      )}

      {mutation.isPending && !rec && (
        <div className="rounded-md border border-zinc-800 bg-zinc-900 px-4 py-8 text-center text-sm text-zinc-400">
          Analyzing your requirements…
        </div>
      )}

      {/* Editable stack controls — always visible once state is seeded or defaults exist */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <label htmlFor="framework" className="block text-xs font-medium text-zinc-400">
            Framework
          </label>
          <select
            id="framework"
            data-testid="framework"
            value={state.framework}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "framework", value: e.target.value })
            }
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          >
            {FRAMEWORK_OPTIONS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="modelPrimary" className="block text-xs font-medium text-zinc-400">
            Primary model
          </label>
          <input
            id="modelPrimary"
            data-testid="modelPrimary"
            type="text"
            value={state.modelPrimary}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "modelPrimary", value: e.target.value })
            }
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="deployCloud" className="block text-xs font-medium text-zinc-400">
            Deploy cloud
          </label>
          <select
            id="deployCloud"
            data-testid="deployCloud"
            value={state.deployCloud}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "deployCloud", value: e.target.value })
            }
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          >
            {CLOUD_OPTIONS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Guidance cards — read-only, non-blocking */}
      {rec && (
        <div className="space-y-3">
          <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">
            Recommended next steps — guidance only, not required to create
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <GuidanceCard
              title="RAG / Knowledge"
              value={rec.rag}
              reasoning={rec.reasoning["rag"]}
            />
            <GuidanceCard
              title="Memory"
              value={rec.memory}
              reasoning={rec.reasoning["memory"]}
            />
            <GuidanceCard
              title="MCP / A2A"
              value={rec.mcp_a2a}
              reasoning={rec.reasoning["mcp_a2a"]}
            />
            <GuidanceCard
              title="Eval dimensions"
              value={rec.eval_dimensions.join(", ")}
              reasoning={rec.reasoning["eval_dimensions"]}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function GuidanceCard({
  title,
  value,
  reasoning,
}: {
  title: string;
  value: string;
  reasoning?: string;
}) {
  return (
    <div
      data-testid={`guidance-${title.toLowerCase().replace(/\s*\/\s*/g, "-").replace(/\s+/g, "-")}`}
      className="rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2.5 space-y-0.5"
    >
      <p className="text-xs font-medium text-zinc-400">{title}</p>
      <p className="text-sm font-medium text-zinc-200">{value || "none"}</p>
      {reasoning && <p className="text-xs text-zinc-500">{reasoning}</p>}
    </div>
  );
}

// Re-export for test access
export { deployTargetToCloud };
