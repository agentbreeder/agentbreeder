import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, FileCode, AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { api, type Agent } from "@/lib/api";
import { Button } from "@/components/ui/button";

const PLACEHOLDER_YAML = `name: my-agent
version: 0.1.0
description: "..."
team: engineering
owner: me@company.com
tags: [example]

framework: google_adk

model:
  primary: gemini-2.5-flash

# Tools resolved from the registry by ref. The ref is what
# engine.tool_resolver looks up at agent startup.
tools:
  - ref: tools/web-search
  - ref: tools/markdown-writer

# System prompt resolved from the registry by ref.
prompts:
  system: prompts/my-agent-system

deploy:
  cloud: gcp
  runtime: cloud-run
  region: us-central1
  secrets:
    - GOOGLE_API_KEY
    - AGENT_AUTH_TOKEN
`;

export default function AgentRegisterPage() {
  const navigate = useNavigate();
  const [yamlContent, setYamlContent] = useState<string>(PLACEHOLDER_YAML);
  const [submitting, setSubmitting] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationOk, setValidationOk] = useState<boolean | null>(null);
  const [validationMessage, setValidationMessage] = useState<string>("");
  const [submitError, setSubmitError] = useState<string>("");

  const handleValidate = async (): Promise<void> => {
    setValidating(true);
    setValidationOk(null);
    setValidationMessage("");
    try {
      const resp = await api.agents.validate(yamlContent);
      const result = resp.data;
      setValidationOk(result.valid);
      setValidationMessage(
        result.valid
          ? "agent.yaml is valid."
          : (result.errors ?? [])
              .map((e: unknown) => {
                if (typeof e === "object" && e !== null && "message" in e) {
                  return String((e as { message: unknown }).message);
                }
                return String(e);
              })
              .join("; "),
      );
    } catch (e) {
      setValidationOk(false);
      setValidationMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setValidating(false);
    }
  };

  const handleSubmit = async (): Promise<void> => {
    setSubmitting(true);
    setSubmitError("");
    try {
      const resp = await api.agents.fromYaml(yamlContent);
      const agent: Agent = resp.data;
      navigate(`/agents/${agent.id}`);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <Link
        to="/agents"
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Back to agents
      </Link>

      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <FileCode className="size-5" />
          Register agent from YAML
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste an <code className="rounded bg-muted px-1 text-xs">agent.yaml</code> below.
          Tools and prompts are resolved from the registry by ref —{" "}
          <Link to="/tools" className="underline">browse tools</Link> or{" "}
          <Link to="/prompts" className="underline">browse prompts</Link> first if you
          haven't pushed them yet. The CLI equivalent is{" "}
          <code className="rounded bg-muted px-1 text-xs">agentbreeder registry agent push agent.yaml</code>.
        </p>
      </div>

      <div className="space-y-3">
        <textarea
          value={yamlContent}
          onChange={(e) => {
            setYamlContent(e.target.value);
            setValidationOk(null);
            setSubmitError("");
          }}
          spellCheck={false}
          className="h-[480px] w-full resize-none rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed outline-none focus:ring-2 focus:ring-ring"
          placeholder="Paste your agent.yaml here…"
        />

        {validationOk !== null && (
          <div
            className={
              "flex items-start gap-2 rounded-md border p-3 text-xs " +
              (validationOk
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                : "border-destructive/30 bg-destructive/10 text-destructive")
            }
          >
            {validationOk ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
            )}
            <span className="whitespace-pre-wrap">{validationMessage}</span>
          </div>
        )}

        {submitError && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span className="whitespace-pre-wrap">{submitError}</span>
          </div>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleValidate}
            disabled={validating || submitting || !yamlContent.trim()}
            className="gap-1.5"
          >
            {validating && <Loader2 className="size-3.5 animate-spin" />}
            Validate
          </Button>
          <Button
            size="sm"
            onClick={handleSubmit}
            disabled={submitting || !yamlContent.trim()}
            className="gap-1.5"
          >
            {submitting && <Loader2 className="size-3.5 animate-spin" />}
            Register agent
          </Button>
        </div>
      </div>
    </div>
  );
}
