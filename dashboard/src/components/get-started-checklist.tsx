/**
 * GetStartedChecklist — 4-step onboarding panel shown on the Home page.
 *
 * Tracks real progress via live API queries + a localStorage flag:
 *   1. Connect a model       → providers.list({status:"active"}) meta.total > 0
 *                              (providers.list is not paginated; reads default page)
 *   2. Create your first agent → agents.list({per_page:1}) meta.total > 0
 *   3. Test in the Playground → localStorage["ag-playground-used-v1"] === "1"
 *   4. Deploy (optional)     → deploys.list({per_page:1}) meta.total > 0
 *
 * Self-hides when all four are done OR the user dismisses (persisted).
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle, Lock, X } from "lucide-react";
import {
  Card,
  CardHeader,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Storage keys
// ---------------------------------------------------------------------------
const DISMISS_KEY = "ag-getstarted-dismissed-v1";
const PLAYGROUND_KEY = "ag-playground-used-v1";

function readDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

function readPlaygroundUsed(): boolean {
  try {
    return localStorage.getItem(PLAYGROUND_KEY) === "1";
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Step state type
// ---------------------------------------------------------------------------
type StepState = "done" | "active" | "locked";

interface Step {
  id: string;
  label: string;
  description: string;
  done: boolean;
  ctaLabel: string;
  ctaHref: string;
  optional?: boolean;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
const STATE_ICON: Record<StepState, React.ReactNode> = {
  done: <CheckCircle2 className="size-5 shrink-0 text-emerald-500" />,
  active: <Circle className="size-5 shrink-0 text-primary" />,
  locked: <Lock className="size-4 shrink-0 text-zinc-600" />,
};

// Only the "done" badge variant is rendered (the Badge is guarded by `state === "done"`).
const DONE_BADGE_VARIANT = "default" as const;

function StepRow({
  step,
  state,
}: {
  step: Step;
  state: StepState;
}) {
  return (
    <div
      data-testid={`step-${step.id}`}
      data-state={state}
      className={cn(
        "flex items-start gap-3 rounded-md px-3 py-3 transition-colors",
        state === "active" && "bg-muted/40",
        state === "locked" && "opacity-50",
      )}
    >
      <span className="mt-0.5">{STATE_ICON[state]}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={cn(
              "text-sm font-medium",
              state === "done" && "line-through text-muted-foreground",
              state === "locked" && "text-zinc-500",
            )}
          >
            {step.label}
          </span>
          {step.optional && (
            <Badge variant="outline" className="text-xs px-1.5 py-0">
              optional
            </Badge>
          )}
          {state === "done" && (
            <Badge
              variant={DONE_BADGE_VARIANT}
              className="bg-emerald-500/20 text-emerald-400 border-0 text-xs px-1.5 py-0"
            >
              done
            </Badge>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">{step.description}</p>
        {(state === "active") && (
          <Link
            to={step.ctaHref}
            data-testid={`cta-${step.id}`}
            className={cn(buttonVariants({ variant: "default", size: "sm" }), "mt-2 inline-flex")}
          >
            {step.ctaLabel}
          </Link>
        )}
        {state === "done" && (
          <Link
            to={step.ctaHref}
            data-testid={`cta-${step.id}`}
            className="mt-1 inline-block text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {step.ctaLabel}
          </Link>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------
export function GetStartedChecklist() {
  const [dismissed, setDismissed] = useState<boolean>(() => readDismissed());

  // Live queries — agents and deploys use per_page=1 tiny fetches to get
  // meta.total; providers.list is not paginated so it reads meta.total from
  // the default page.
  const providersQuery = useQuery({
    queryKey: ["onboarding-providers"],
    queryFn: () => api.providers.list({ status: "active" }),
    staleTime: 30_000,
  });

  const agentsQuery = useQuery({
    queryKey: ["onboarding-agents"],
    queryFn: () => api.agents.list({ per_page: 1 }),
    staleTime: 30_000,
  });

  const deploysQuery = useQuery({
    queryKey: ["onboarding-deploys"],
    queryFn: () => api.deploys.list({ per_page: 1 }),
    staleTime: 30_000,
  });

  // Signals
  const providersDone = (providersQuery.data?.meta.total ?? 0) > 0;
  const agentsDone = (agentsQuery.data?.meta.total ?? 0) > 0;
  const playgroundDone = readPlaygroundUsed();
  const deploysDone = (deploysQuery.data?.meta.total ?? 0) > 0;

  const allDone = providersDone && agentsDone && playgroundDone && deploysDone;

  // While any query is still loading, render nothing so returning
  // fully-onboarded users don't see a wrong-state flash of the checklist.
  if (providersQuery.isPending || agentsQuery.isPending || deploysQuery.isPending) return null;

  // Hide when dismissed or all done.
  if (dismissed || allDone) return null;

  // Build steps
  const steps: Step[] = [
    {
      id: "connect-model",
      label: "Connect a model",
      description: "Add an LLM provider (OpenAI, Anthropic, Ollama…) so your agents have a brain.",
      done: providersDone,
      ctaLabel: "Add a model provider",
      ctaHref: "/models",
    },
    {
      id: "create-agent",
      label: "Create your first agent",
      description: "Define your agent's persona, tools, and guardrails in the visual builder.",
      done: agentsDone,
      ctaLabel: "Create your first agent",
      ctaHref: "/agents/builder",
    },
    {
      id: "test-playground",
      label: "Test it in the Playground",
      description: "Chat with your agent in a sandboxed environment before going live.",
      done: playgroundDone,
      ctaLabel: "Open Playground",
      ctaHref: "/playground",
    },
    {
      id: "deploy",
      label: "Deploy — or keep local",
      description: "Push your agent to AWS, GCP, Azure, or run it locally. Your call.",
      done: deploysDone,
      ctaLabel: "Deploy now",
      ctaHref: "/deploys",
      optional: true,
    },
  ];

  // Derive step states: done → done, first non-done → active, rest → locked.
  let foundActive = false;
  const stepStates: StepState[] = steps.map((step) => {
    if (step.done) return "done";
    if (!foundActive) {
      foundActive = true;
      return "active";
    }
    return "locked";
  });

  function handleDismiss() {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // private browsing — fine
    }
    setDismissed(true);
  }

  return (
    <Card className="mb-8 border-border bg-card relative z-10">
      <CardHeader className="pb-2 flex flex-row items-start justify-between gap-2">
        <h2 className="font-display text-h2 font-extrabold leading-tight text-foreground">
          Welcome — let's ship your first agent
        </h2>
        <button
          data-testid="checklist-dismiss"
          onClick={handleDismiss}
          aria-label="Dismiss checklist"
          className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:text-foreground hover:bg-muted"
        >
          <X className="size-4" />
        </button>
      </CardHeader>
      <CardContent className="pt-1">
        <div className="space-y-1">
          {steps.map((step, i) => (
            <StepRow key={step.id} step={step} state={stepStates[i]} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
