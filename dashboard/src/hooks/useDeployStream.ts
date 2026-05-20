import { useEffect, useRef, useState } from "react";
import type { DeployEvent } from "@/lib/deploy-events";

interface Options {
  onEvent?: (e: DeployEvent) => void;
}

interface State {
  status: "connecting" | "open" | "disconnected";
}

const BACKOFF_MS = [500, 2000, 5000];

export function useDeployStream(
  jobId: string | null,
  opts: Options = {},
): State {
  const [state, setState] = useState<State>({ status: "connecting" });
  const retriesRef = useRef(0);
  const onEventRef = useRef(opts.onEvent);
  onEventRef.current = opts.onEvent;

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let timer: number | null = null;

    function connect(): void {
      es = new EventSource(`/api/v1/deployments/${jobId}/stream`);
      es.addEventListener("open", () => {
        retriesRef.current = 0;
        setState({ status: "open" });
      });
      for (const t of ["phase", "log", "complete", "error", "ping"] as const) {
        es.addEventListener(t, (ev: MessageEvent) => {
          if (t === "ping") return;
          try {
            onEventRef.current?.(JSON.parse(ev.data));
          } catch {
            /* ignore malformed event */
          }
        });
      }
      es.onerror = () => {
        if (cancelled) return;
        es?.close();
        es = null;
        const i = retriesRef.current;
        if (i >= BACKOFF_MS.length) {
          setState({ status: "disconnected" });
          return;
        }
        retriesRef.current = i + 1;
        timer = window.setTimeout(connect, BACKOFF_MS[i]);
      };
    }
    connect();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
      es?.close();
    };
  }, [jobId]);

  return state;
}
