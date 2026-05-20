import { useState } from "react";
import type {
  Action,
  Cloud,
  DeployWizardState,
} from "@/lib/deploy-wizard-state";
import { COST_TABLE, estimateMonthly } from "@/lib/deploy-wizard-cost";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

const CLOUDS: { id: Cloud; label: string; blurb: string }[] = [
  { id: "aws", label: "AWS", blurb: "ECS Fargate · per-vCPU pricing" },
  { id: "gcp", label: "GCP", blurb: "Cloud Run · pay-per-request" },
  { id: "azure", label: "Azure", blurb: "Container Apps · pay-per-request" },
];

export function Step2Target({ state, dispatch }: Props) {
  // Cloud is staged locally until the user picks a region; only THEN do we dispatch.
  // If state already has a cloud (e.g. after going back), pre-seed.
  const [stagedCloud, setStagedCloud] = useState<Cloud | null>(state.cloud);

  const regions = stagedCloud
    ? Object.keys(COST_TABLE[stagedCloud] as Record<string, unknown>)
    : [];

  const cost =
    state.cloud && state.region
      ? estimateMonthly(state.cloud, state.region, {
          hasMemory: !!state.agentSnapshot?.declaresMemory,
          isPublic: false,
        })
      : null;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Step 2 — Cloud target</h2>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {CLOUDS.map((c) => {
          const selected = stagedCloud === c.id || state.cloud === c.id;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => setStagedCloud(c.id)}
              className={`p-3 border rounded text-left transition-colors ${
                selected
                  ? "border-emerald-500 bg-emerald-500/10"
                  : "border-zinc-800 hover:border-zinc-700"
              }`}
            >
              <div className="font-medium">{c.label}</div>
              <div className="text-xs text-zinc-400">{c.blurb}</div>
            </button>
          );
        })}
      </div>

      {stagedCloud && (
        <div className="space-y-2">
          <label
            htmlFor="region-select"
            className="block text-sm text-zinc-400"
          >
            Region
          </label>
          <select
            id="region-select"
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
            value={state.cloud === stagedCloud ? (state.region ?? "") : ""}
            onChange={(e) => {
              if (!e.target.value) return;
              dispatch({
                type: "SET_CLOUD_REGION",
                cloud: stagedCloud,
                region: e.target.value,
              });
            }}
          >
            <option value="">Choose a region…</option>
            {regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
      )}

      {state.cloud && state.region && (
        <div className="rounded border border-zinc-800 p-3 text-sm">
          <div className="font-medium mb-1">Cost estimate</div>
          {cost?.status === "unsupported" ? (
            <p className="text-amber-400 text-xs">
              Cost estimate unavailable for this region — you can still
              proceed.
            </p>
          ) : cost ? (
            <>
              <div>
                <span className="font-mono">${cost.low}–${cost.high}/mo</span>{" "}
                <span className="text-zinc-500 text-xs">(±10%)</span>
              </div>
              <ul className="text-xs text-zinc-400 mt-1 space-y-0.5">
                {cost.lines.map((l) => (
                  <li key={l.resource}>
                    {l.resource} — ${l.usd}/mo
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
