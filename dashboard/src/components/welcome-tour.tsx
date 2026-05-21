/**
 * WelcomeTour — first-run 4-step guided tour for AgentBreeder Studio.
 *
 * Driven by `use-tour` (localStorage-backed). On first sign-in the tour
 * auto-opens; the user can dismiss at any step. The "Restart tour" link
 * in the shell footer re-opens it on demand.
 *
 * Issue #465 / tracker #461. Intentionally low-friction: a centered modal
 * panel with content + nav buttons. No DOM-spotlight overlay (that would
 * couple the tour to specific element positions across 46 pages); each
 * step has a Visit-page CTA that deep-links instead.
 */
import { useState, useCallback, useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  ArrowLeft,
  Bot,
  MessageSquare,
  Hammer,
  Shield,
  X,
  Check,
} from "lucide-react";
import { useTour } from "@/hooks/use-tour";
import { cn } from "@/lib/utils";

interface TourStep {
  icon: typeof Bot;
  title: string;
  body: ReactNode;
  /** Optional CTA that navigates the user to the highlighted surface. */
  cta?: { label: string; path: string };
}

const STEPS: TourStep[] = [
  {
    icon: Bot,
    title: "Welcome to AgentBreeder Studio",
    body: (
      <>
        <p>
          You're looking at the place where agents live in your org —
          registry, builder, deploys, costs, audit, and the playground all
          under one roof.
        </p>
        <p className="mt-3">
          We&apos;ve seeded <span className="font-semibold">5 sample agents</span>{" "}
          so you have something to try right away.
        </p>
      </>
    ),
    cta: { label: "See the agents", path: "/agents" },
  },
  {
    icon: MessageSquare,
    title: "Try chatting with one",
    body: (
      <>
        <p>
          The Playground lets you talk to any deployed agent without writing
          any code. Pick one from the dropdown, ask a question, and watch
          tool calls + traces stream back in real time.
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          Tip: the <span className="font-mono">assistant</span> agent is a
          good starting point.
        </p>
      </>
    ),
    cta: { label: "Open Playground", path: "/playground" },
  },
  {
    icon: Hammer,
    title: "Or build your own",
    body: (
      <>
        <p>
          The visual agent builder ships a drag-and-drop canvas with 8 node
          types — model, tool, MCP server, RAG, memory, prompt, guardrail,
          handoff. No YAML required.
        </p>
        <p className="mt-3">
          When you&apos;re happy with it, <span className="font-semibold">Deploy</span>{" "}
          runs an 8-step pipeline (parse → RBAC → resolve deps → build → provision →
          health-check → register → return endpoint) — atomic, with rollback on
          any failure.
        </p>
      </>
    ),
    cta: { label: "Open the builder", path: "/agents/builder" },
  },
  {
    icon: Shield,
    title: "Everything you do is governed",
    body: (
      <>
        <p>
          Every deploy, every LLM call, every tool execution shows up
          automatically in <span className="font-semibold">Costs</span>{" "}
          (per token, per team) and the{" "}
          <span className="font-semibold">Audit Log</span> (every action,
          immutable, exportable).
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          Governance is a side effect of using Studio — never extra
          configuration you have to remember.
        </p>
      </>
    ),
    cta: { label: "See Costs", path: "/costs" },
  },
];

export function WelcomeTour() {
  const { isOpen, dismiss } = useTour();
  const navigate = useNavigate();
  const [index, setIndex] = useState(0);

  // Reset to step 0 each time the tour opens.
  useEffect(() => {
    if (isOpen) setIndex(0);
  }, [isOpen]);

  // Allow Esc to dismiss.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, dismiss]);

  const handleVisit = useCallback(
    (path: string) => {
      dismiss();
      navigate(path);
    },
    [dismiss, navigate],
  );

  if (!isOpen) return null;

  const step = STEPS[index];
  const isFirst = index === 0;
  const isLast = index === STEPS.length - 1;
  const Icon = step.icon;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="welcome-tour-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4 backdrop-blur-sm"
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card text-card-foreground shadow-2xl">
        {/* Top-right dismiss */}
        <button
          type="button"
          onClick={dismiss}
          aria-label="Close tour"
          className="absolute right-3 top-3 grid size-7 place-items-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>

        {/* Step content */}
        <div className="px-6 pb-4 pt-6">
          <div className="mb-4 flex size-11 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/20">
            <Icon className="size-5 text-primary" />
          </div>
          <h2 id="welcome-tour-title" className="text-lg font-semibold tracking-tight">
            {step.title}
          </h2>
          <div className="mt-2 space-y-2 text-sm text-muted-foreground">
            {step.body}
          </div>
        </div>

        {/* Progress dots */}
        <div className="flex items-center justify-center gap-1.5 px-6 py-2">
          {STEPS.map((_, i) => (
            <span
              key={i}
              aria-hidden
              className={cn(
                "size-1.5 rounded-full transition",
                i === index ? "bg-primary" : "bg-muted",
              )}
            />
          ))}
        </div>

        {/* Nav */}
        <div className="flex items-center justify-between gap-2 border-t border-border bg-muted/30 px-4 py-3">
          <button
            type="button"
            onClick={dismiss}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Skip tour
          </button>
          <div className="flex items-center gap-2">
            {!isFirst && (
              <button
                type="button"
                onClick={() => setIndex((i) => i - 1)}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium transition hover:bg-muted"
              >
                <ArrowLeft className="size-3.5" />
                Back
              </button>
            )}
            {step.cta && (
              <button
                type="button"
                onClick={() => handleVisit(step.cta!.path)}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium transition hover:bg-muted"
              >
                {step.cta.label}
              </button>
            )}
            {isLast ? (
              <button
                type="button"
                onClick={dismiss}
                className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:bg-primary/90"
              >
                <Check className="size-3.5" />
                Done
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setIndex((i) => i + 1)}
                className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:bg-primary/90"
              >
                Next
                <ArrowRight className="size-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
