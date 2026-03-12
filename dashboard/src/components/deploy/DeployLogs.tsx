/**
 * DeployLogs — scrollable log output panel for deployment progress.
 * Auto-scrolls to bottom as new logs arrive. Shows timestamp, level, and message.
 */

import { useRef, useEffect } from "react";
import {
  Terminal,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  step: string | null;
}

interface DeployLogsProps {
  logs: LogEntry[];
  expanded: boolean;
  onToggle: () => void;
}

const LEVEL_COLORS: Record<string, string> = {
  info: "text-sky-400",
  warn: "text-amber-400",
  error: "text-red-400",
  debug: "text-muted-foreground/60",
};

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "--:--:--";
  }
}

export function DeployLogs({ logs, expanded, onToggle }: DeployLogsProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (scrollRef.current && expanded) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs.length, expanded]);

  return (
    <div className="border-t border-border bg-zinc-950">
      {/* Toggle header */}
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-3 py-1.5 text-[10px] font-medium text-zinc-400 transition-colors hover:text-zinc-200"
      >
        <span className="flex items-center gap-1.5">
          <Terminal className="size-3" />
          Deploy Logs
          {logs.length > 0 && (
            <span className="rounded-full bg-zinc-800 px-1.5 py-0.5 text-[9px] tabular-nums">
              {logs.length}
            </span>
          )}
        </span>
        {expanded ? (
          <ChevronDown className="size-3" />
        ) : (
          <ChevronUp className="size-3" />
        )}
      </button>

      {/* Log output */}
      {expanded && (
        <div
          ref={scrollRef}
          className="max-h-48 overflow-y-auto border-t border-zinc-800 px-3 py-2 font-mono text-[11px] leading-5"
        >
          {logs.length === 0 ? (
            <p className="text-zinc-600">Waiting for deploy to start...</p>
          ) : (
            logs.map((entry, i) => (
              <div key={i} className="flex gap-2">
                <span className="shrink-0 text-zinc-600 select-none">
                  {formatTimestamp(entry.timestamp)}
                </span>
                <span
                  className={cn(
                    "shrink-0 w-10 uppercase select-none",
                    LEVEL_COLORS[entry.level] ?? "text-zinc-500"
                  )}
                >
                  {entry.level}
                </span>
                <span className="text-zinc-300">{entry.message}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
