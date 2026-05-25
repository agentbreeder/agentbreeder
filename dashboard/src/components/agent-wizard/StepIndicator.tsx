import { cn } from "@/lib/utils";
import type { WizardStep } from "@/lib/agent-wizard-state";

interface Props {
  current: WizardStep;
  canAdvanceTo: (n: WizardStep) => boolean;
  onJump: (n: WizardStep) => void;
}

const LABELS = ["Goal", "Workflow", "Stack", "Create"] as const;

export function StepIndicator({ current, canAdvanceTo, onJump }: Props) {
  return (
    <ol className="flex items-center gap-2" aria-label="Wizard steps">
      {([1, 2, 3, 4] as const).map((n, i) => {
        const reachable = canAdvanceTo(n);
        const isActive = n === current;
        const isPast = n < current;
        return (
          <li key={n} className="flex items-center gap-2">
            <button
              type="button"
              disabled={!reachable}
              onClick={() => reachable && onJump(n)}
              className={cn(
                "h-7 w-7 rounded-full border text-xs flex items-center justify-center transition-colors",
                isActive && "bg-emerald-500 text-black border-emerald-500",
                isPast && !isActive && "bg-emerald-500/20 border-emerald-500/40 text-emerald-300",
                !isActive && !isPast && "border-zinc-700 text-zinc-400",
                !reachable && "cursor-not-allowed opacity-50",
              )}
              aria-current={isActive ? "step" : undefined}
              aria-label={`Step ${n}: ${LABELS[i]}`}
            >
              {n}
            </button>
            {i < 3 && <span className="h-px w-8 bg-zinc-700" />}
          </li>
        );
      })}
    </ol>
  );
}
