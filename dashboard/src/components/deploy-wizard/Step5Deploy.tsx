import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useDeployStream } from "@/hooks/useDeployStream";
import type { DeployEvent } from "@/lib/deploy-events";
import type { Action, DeployWizardState } from "@/lib/deploy-wizard-state";

interface Props {
  state: DeployWizardState;
  dispatch: (a: Action) => void;
}

const PHASES = [
  "provisioning",
  "building",
  "pushing",
  "deploying",
  "health_checking",
  "registering",
] as const;

export function Step5Deploy({ state, dispatch }: Props) {
  const [logs, setLogs] = useState<string[]>([]);

  const { status: streamStatus } = useDeployStream(state.jobId, {
    onEvent: (e: DeployEvent) => {
      if (e.type === "log" && e.message) {
        setLogs((prev) => [...prev, e.message!]);
      }
      dispatch({ type: "SSE_EVENT", event: e });
    },
  });

  // Polling fallback when approval pending or stream disconnected.
  useEffect(() => {
    if (!state.jobId) return;
    if (streamStatus === "open" && !state.approvalPending) return;

    let stopped = false;
    async function poll(): Promise<void> {
      while (!stopped) {
        try {
          const resp = await api.deployments.getJob(state.jobId!);
          const stat = resp.data.status;
          if (stat === "completed" || stat === "failed" || stat === "timed_out") {
            // Synthesize a terminal SSE event to drive reducer.
            dispatch({
              type: "SSE_EVENT",
              event: {
                type: stat === "completed" ? "complete" : "error",
                job_id: state.jobId!,
                timestamp: new Date().toISOString(),
                phase: null,
                step: null,
                total: null,
                message: null,
                level: null,
                endpoint_url: resp.data.endpoint_url ?? null,
                error_code: null,
              } as DeployEvent,
            });
            return;
          }
        } catch {
          /* ignore transient errors */
        }
        await new Promise((res) => setTimeout(res, 4000));
      }
    }
    poll();
    return () => {
      stopped = true;
    };
  }, [state.jobId, streamStatus, state.approvalPending, dispatch]);

  const rollback = useMutation({
    mutationFn: () => api.deployments.destroyPartial(state.jobId!),
  });

  // Find the current phase index in PHASES.
  const phaseIdx = PHASES.indexOf(state.jobStatus as (typeof PHASES)[number]);
  const isTerminal =
    state.jobStatus === "completed" ||
    state.jobStatus === "failed" ||
    state.jobStatus === "timed_out";

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Step 5 — Live deploy</h2>

      {state.approvalPending && state.jobStatus === "pending_approval" && (
        <div className="border border-amber-500/40 bg-amber-500/10 rounded p-3 text-sm">
          <p className="font-medium">Awaiting admin approval</p>
          <p className="text-xs text-amber-300 mt-1">
            Your deploy is queued in the approvals system. This page will switch to
            the live stream once an admin approves.
          </p>
        </div>
      )}

      <ol className="grid grid-cols-3 sm:grid-cols-6 gap-2 text-xs">
        {PHASES.map((p, i) => {
          const isCurrent = state.jobStatus === p;
          const isPast = phaseIdx > i && !isTerminal;
          return (
            <li
              key={p}
              className={`p-2 border rounded text-center ${
                isCurrent
                  ? "border-emerald-500 bg-emerald-500/10 text-emerald-300"
                  : isPast
                  ? "border-emerald-500/30 text-emerald-400"
                  : "border-zinc-800 text-zinc-500"
              }`}
            >
              {p}
            </li>
          );
        })}
      </ol>

      {logs.length > 0 && (
        <div className="font-mono text-xs bg-zinc-950 border border-zinc-800 rounded p-2 h-40 overflow-y-auto">
          {logs.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      )}

      {state.jobStatus === "completed" && state.endpointUrl && (
        <div className="border border-emerald-500/40 bg-emerald-500/10 rounded p-3 text-sm">
          <p className="font-medium text-emerald-300">Deployed</p>
          <div className="flex items-center gap-2 mt-1">
            <code className="text-xs">{state.endpointUrl}</code>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(state.endpointUrl!)}
              className="text-xs text-emerald-400 hover:underline"
            >
              Copy
            </button>
          </div>
        </div>
      )}

      {state.jobStatus === "failed" && (
        <div className="border border-red-500/40 bg-red-500/10 rounded p-3 text-sm space-y-2">
          <p className="font-medium text-red-300">Deploy failed</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => rollback.mutate()}
              disabled={rollback.isPending}
              className="text-xs border border-red-500/50 rounded px-2 py-1 hover:bg-red-500/10"
            >
              Roll back
            </button>
            <button
              type="button"
              onClick={() => dispatch({ type: "RESET" })}
              className="text-xs border border-zinc-700 rounded px-2 py-1 hover:bg-zinc-800"
            >
              Start over
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
