import { useState } from "react";
import { Sparkles, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "A support agent that reads our docs and Zendesk",
  "A daily news digest agent emailed to the team",
  "An invoice-processing agent that extracts line items",
];

export function BuildFrontDoor({ onStart }: { onStart: (prompt: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center gap-6 py-12 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-primary/10">
        <Sparkles className="size-6 text-primary" />
      </div>
      <h1 className="text-2xl font-semibold">What do you want to build today?</h1>
      <p className="text-sm text-muted-foreground">
        Describe your agent in plain language. I&apos;ll ask a few questions, generate a
        ready-to-deploy <span className="font-mono">agent.yaml</span>, and ship it.
      </p>
      <form
        data-testid="frontdoor-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onStart(value.trim());
        }}
        className="flex w-full items-center gap-2"
      >
        <input
          data-testid="frontdoor-input"
          aria-label="Describe the agent you want to build"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. a customer-support agent for our SaaS"
          className={cn(
            "flex-1 rounded-lg border border-border bg-background px-4 py-3 text-sm",
            "focus:outline-none focus:ring-2 focus:ring-primary/50",
          )}
        />
        <Button type="submit" disabled={!value.trim()} size="icon" className="shrink-0">
          <ArrowRight className="size-4" />
        </Button>
      </form>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => onStart(ex)}
            className="rounded-full border border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
