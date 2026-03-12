/**
 * SandboxRunner — "Run Tool" panel for executing tool code in an isolated sandbox.
 *
 * Features:
 * - Input JSON editor
 * - Timeout configuration + network toggle
 * - "Run Tool" button calling POST /api/v1/tools/sandbox/execute
 * - Output display: result JSON, stdout/stderr tabs, exit code badge
 * - Execution history (stored in component state)
 */
import { useState, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Play,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  Wifi,
  WifiOff,
  Terminal,
  FileOutput,
  History,
  Trash2,
} from "lucide-react";
import { api, type SandboxExecuteResponse } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// --- Types ---

interface ExecutionHistoryEntry {
  id: string;
  timestamp: string;
  code: string;
  input: Record<string, unknown>;
  result: SandboxExecuteResponse;
}

type OutputTab = "output" | "stdout" | "stderr";

// --- Component ---

export function SandboxRunner({ code }: { code: string }) {
  // Input state
  const [inputJson, setInputJson] = useState("{}");
  const [inputError, setInputError] = useState<string | null>(null);
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [networkEnabled, setNetworkEnabled] = useState(false);

  // Output state
  const [activeTab, setActiveTab] = useState<OutputTab>("output");
  const [lastResult, setLastResult] = useState<SandboxExecuteResponse | null>(null);

  // History
  const [history, setHistory] = useState<ExecutionHistoryEntry[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  // Execution mutation
  const executeMutation = useMutation({
    mutationFn: async () => {
      let parsedInput: Record<string, unknown>;
      try {
        parsedInput = JSON.parse(inputJson);
        setInputError(null);
      } catch {
        setInputError("Invalid JSON");
        throw new Error("Invalid input JSON");
      }

      const resp = await api.sandbox.execute({
        code,
        input_json: parsedInput,
        timeout_seconds: timeoutSeconds,
        network_enabled: networkEnabled,
      });
      return resp.data;
    },
    onSuccess: (result) => {
      setLastResult(result);
      setActiveTab("output");

      // Add to history
      const entry: ExecutionHistoryEntry = {
        id: result.execution_id,
        timestamp: new Date().toISOString(),
        code,
        input: JSON.parse(inputJson),
        result,
      };
      setHistory((prev) => [entry, ...prev].slice(0, 20));
    },
  });

  const handleRun = useCallback(() => {
    executeMutation.mutate();
  }, [executeMutation]);

  const clearHistory = useCallback(() => {
    setHistory([]);
  }, []);

  const restoreFromHistory = useCallback((entry: ExecutionHistoryEntry) => {
    setInputJson(JSON.stringify(entry.input, null, 2));
    setLastResult(entry.result);
    setShowHistory(false);
  }, []);

  const isRunning = executeMutation.isPending;

  return (
    <div className="flex h-full flex-col space-y-4">
      {/* Input JSON editor */}
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Input JSON
        </h3>
        <textarea
          value={inputJson}
          onChange={(e) => {
            setInputJson(e.target.value);
            setInputError(null);
          }}
          spellCheck={false}
          placeholder='{"key": "value"}'
          className={cn(
            "h-32 w-full resize-none rounded-md border bg-muted/30 p-2.5 font-mono text-xs leading-relaxed outline-none focus:ring-2 focus:ring-ring/50",
            inputError
              ? "border-destructive focus:border-destructive"
              : "border-input focus:border-ring"
          )}
        />
        {inputError && (
          <p className="mt-1 text-[10px] text-destructive">{inputError}</p>
        )}
      </div>

      {/* Configuration */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className="mb-1 flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
            <Clock className="size-2.5" />
            Timeout (s)
          </label>
          <Input
            type="number"
            min={1}
            max={300}
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 30)}
            className="h-7 text-xs"
          />
        </div>
        <div className="flex-1">
          <label className="mb-1 flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
            {networkEnabled ? (
              <Wifi className="size-2.5" />
            ) : (
              <WifiOff className="size-2.5" />
            )}
            Network
          </label>
          <button
            onClick={() => setNetworkEnabled(!networkEnabled)}
            className={cn(
              "flex h-7 w-full items-center justify-center gap-1.5 rounded-md border text-xs font-medium transition-colors",
              networkEnabled
                ? "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-400"
                : "border-input bg-background text-muted-foreground hover:text-foreground"
            )}
          >
            {networkEnabled ? "Enabled" : "Disabled"}
          </button>
        </div>
      </div>

      {/* Run button */}
      <button
        onClick={handleRun}
        disabled={isRunning || !code.trim()}
        className="flex w-full items-center justify-center gap-1.5 rounded-md bg-foreground px-3 py-2 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
      >
        {isRunning ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <Play className="size-3" />
        )}
        {isRunning ? "Executing..." : "Run Tool"}
      </button>

      {/* Error from mutation */}
      {executeMutation.error && !inputError && (
        <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-[10px] text-destructive">
          <XCircle className="size-3 shrink-0" />
          {(executeMutation.error as Error).message}
        </div>
      )}

      {/* Output display */}
      {lastResult && (
        <div className="space-y-2">
          {/* Status bar */}
          <div className="flex items-center gap-2">
            {lastResult.exit_code === 0 ? (
              <Badge
                variant="outline"
                className="gap-1 border-emerald-300 text-emerald-700 text-[10px]"
              >
                <CheckCircle2 className="size-2.5" />
                Exit 0
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="gap-1 border-destructive text-destructive text-[10px]"
              >
                <XCircle className="size-2.5" />
                Exit {lastResult.exit_code}
              </Badge>
            )}
            {lastResult.timed_out && (
              <Badge
                variant="outline"
                className="gap-1 border-amber-300 text-amber-700 text-[10px]"
              >
                <AlertTriangle className="size-2.5" />
                Timed out
              </Badge>
            )}
            <span className="ml-auto font-mono text-[10px] text-muted-foreground">
              {lastResult.duration_ms}ms
            </span>
          </div>

          {/* Tab switcher */}
          <div className="flex items-center gap-0.5 rounded-md bg-muted/40 p-0.5">
            {(
              [
                { key: "output" as OutputTab, label: "Output", icon: FileOutput },
                { key: "stdout" as OutputTab, label: "stdout", icon: Terminal },
                { key: "stderr" as OutputTab, label: "stderr", icon: AlertTriangle },
              ] as const
            ).map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setActiveTab(key)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-[10px] font-medium transition-colors",
                  activeTab === key
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="size-2.5" />
                {label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <pre className="max-h-48 overflow-auto rounded-md border border-border bg-muted/30 p-2.5 font-mono text-[10px] leading-relaxed">
            {activeTab === "output" && (lastResult.output || "(no output)")}
            {activeTab === "stdout" && (lastResult.stdout || "(empty)")}
            {activeTab === "stderr" && (lastResult.stderr || "(empty)")}
          </pre>

          {lastResult.error && (
            <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-[10px] text-destructive">
              <XCircle className="size-3 shrink-0" />
              {lastResult.error}
            </div>
          )}
        </div>
      )}

      {/* Execution History */}
      <div>
        <div className="flex items-center justify-between">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground hover:text-foreground"
          >
            <History className="size-3" />
            History ({history.length})
          </button>
          {history.length > 0 && (
            <button
              onClick={clearHistory}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="size-2.5" />
              Clear
            </button>
          )}
        </div>

        {showHistory && history.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {history.map((entry) => (
              <button
                key={entry.id}
                onClick={() => restoreFromHistory(entry)}
                className="flex w-full items-center gap-2 rounded-md border border-border p-2 text-left transition-colors hover:bg-muted/50"
              >
                {entry.result.exit_code === 0 ? (
                  <CheckCircle2 className="size-3 shrink-0 text-emerald-500" />
                ) : (
                  <XCircle className="size-3 shrink-0 text-destructive" />
                )}
                <div className="flex-1 truncate">
                  <span className="text-[10px] text-muted-foreground">
                    {new Date(entry.timestamp).toLocaleTimeString()}
                  </span>
                </div>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {entry.result.duration_ms}ms
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
