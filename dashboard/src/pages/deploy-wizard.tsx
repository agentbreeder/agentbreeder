import { useEffect, useReducer, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useDebouncedEffect } from "@/hooks/useDebouncedEffect";
import {
  canAdvance,
  initialState,
  reducer,
  type Origin,
  type Step,
} from "@/lib/deploy-wizard-state";
import { StepIndicator } from "@/components/deploy-wizard/StepIndicator";
import { Step1Agent } from "@/components/deploy-wizard/Step1Agent";
import { Step2Target } from "@/components/deploy-wizard/Step2Target";
import { Step3Infra } from "@/components/deploy-wizard/Step3Infra";
import { Step4Config } from "@/components/deploy-wizard/Step4Config";
import { Step5Deploy } from "@/components/deploy-wizard/Step5Deploy";

const DRAFT_KEY = "deploy-wizard-draft";

export default function DeployWizardPage() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [resumePromptOpen, setResumePromptOpen] = useState(false);
  const [hasHydrated, setHasHydrated] = useState(false);

  // ----- Mount: read query params + localStorage, possibly prompt to resume.
  useEffect(() => {
    const stepParam = Number(searchParams.get("step")) || undefined;
    const agentParam = searchParams.get("agentId") ?? undefined;
    const fromParam = (searchParams.get("from") as Origin | null) ?? undefined;

    if (agentParam || fromParam || stepParam) {
      dispatch({
        type: "PREFILL_FROM_QUERY",
        agentId: agentParam,
        from: fromParam,
        step: stepParam as Step | undefined,
      });
    }

    const raw = localStorage.getItem(DRAFT_KEY);
    if (raw) {
      try {
        const draft = JSON.parse(raw);
        // If draft is for a different agent than what's in the URL, prompt.
        if (draft.agentId && (!agentParam || draft.agentId === agentParam)) {
          setResumePromptOpen(true);
        }
      } catch {
        localStorage.removeItem(DRAFT_KEY);
      }
    }
    setHasHydrated(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ----- Persist on change (debounced 250ms).
  useDebouncedEffect(
    () => {
      if (!hasHydrated) return;
      if (state.step === 5 && state.jobStatus === "completed") {
        localStorage.removeItem(DRAFT_KEY);
        return;
      }
      // Don't persist secrets values, validation results, or terminal job status.
      const toPersist = {
        ...state,
        secrets: [],
        validateResult: null,
        jobStatus: null,
      };
      localStorage.setItem(DRAFT_KEY, JSON.stringify(toPersist));
    },
    [state, hasHydrated],
    250,
  );

  // ----- Clamp step via canAdvance: if the current state.step is beyond what's
  //       reachable, snap back to step 1 (which is always reachable).
  // We check by trying to advance FROM step 1 TO state.step. If that fails, clamp to 1.
  useEffect(() => {
    if (!hasHydrated || state.step === 1) return;

    // Create a test state with step=1 to check if we can reach state.step from there.
    // This is valid because each step unlocks when previous steps are completed.
    const testState = { ...state, step: 1 as const };
    if (!canAdvance(testState, state.step)) {
      dispatch({ type: "GOTO", step: 1 });
    }
  }, [hasHydrated, state.step]);

  function handleResumeYes(): void {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (raw) {
      try {
        const draft = JSON.parse(raw);
        dispatch({ type: "HYDRATE_FROM_DRAFT", state: draft });
      } catch {
        /* ignore */
      }
    }
    setResumePromptOpen(false);
  }

  function handleStartOver(): void {
    localStorage.removeItem(DRAFT_KEY);
    dispatch({ type: "RESET" });
    setResumePromptOpen(false);
  }

  function handleNext(): void {
    const next = (state.step + 1) as Step;
    if (canAdvance(state, next)) {
      dispatch({ type: "GOTO", step: next });
      setSearchParams((sp) => {
        sp.set("step", String(next));
        return sp;
      });
    }
  }

  function handleBack(): void {
    if (state.step === 1) return;
    const prev = (state.step - 1) as Step;
    dispatch({ type: "GOTO", step: prev });
    setSearchParams((sp) => {
      sp.set("step", String(prev));
      return sp;
    });
  }

  function handleJump(n: Step): void {
    if (!canAdvance(state, n)) return;
    dispatch({ type: "GOTO", step: n });
    setSearchParams((sp) => {
      sp.set("step", String(n));
      return sp;
    });
  }

  function handleCancel(): void {
    const dest =
      state.origin === "agent-detail" && state.agentId
        ? `/agents/${state.agentId}`
        : state.origin === "deploys"
        ? "/deploys"
        : "/";
    navigate(dest);
  }

  const StepBody = (() => {
    switch (state.step) {
      case 1:
        return <Step1Agent state={state} dispatch={dispatch} />;
      case 2:
        return <Step2Target state={state} dispatch={dispatch} />;
      case 3:
        return <Step3Infra state={state} dispatch={dispatch} />;
      case 4:
        return <Step4Config state={state} dispatch={dispatch} />;
      case 5:
        return <Step5Deploy state={state} dispatch={dispatch} />;
    }
  })();

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      <header className="space-y-3">
        <h1 className="text-xl font-medium">Deploy an agent</h1>
        <StepIndicator
          current={state.step}
          canAdvanceTo={(n) => canAdvance(state, n)}
          onJump={handleJump}
        />
      </header>

      {resumePromptOpen && (
        <div className="border border-amber-500/40 bg-amber-500/10 rounded p-3 text-sm">
          <p className="font-medium">Resume previous deploy?</p>
          <p className="text-xs text-amber-300 mt-1">
            You have an unfinished wizard session saved in this browser.
          </p>
          <div className="flex gap-2 mt-2">
            <button
              type="button"
              onClick={handleResumeYes}
              className="text-xs border border-amber-500/50 rounded px-2 py-1 hover:bg-amber-500/10"
            >
              Resume
            </button>
            <button
              type="button"
              onClick={handleStartOver}
              className="text-xs border border-zinc-700 rounded px-2 py-1 hover:bg-zinc-800"
            >
              Start over
            </button>
          </div>
        </div>
      )}

      <main>{StepBody}</main>

      {state.step < 5 && (
        <footer className="flex justify-between pt-3 border-t border-zinc-800">
          <button
            type="button"
            onClick={handleCancel}
            className="text-sm text-zinc-400 hover:text-zinc-200"
          >
            Cancel
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleBack}
              disabled={state.step === 1}
              className="text-sm border border-zinc-700 rounded px-3 py-1.5 disabled:opacity-50"
            >
              Back
            </button>
            <button
              type="button"
              onClick={handleNext}
              disabled={!canAdvance(state, (state.step + 1) as Step)}
              className="text-sm bg-emerald-500 text-black rounded px-3 py-1.5 disabled:opacity-50"
            >
              Next →
            </button>
          </div>
        </footer>
      )}
    </div>
  );
}
