/**
 * Agent Wizard page — /agents/new
 *
 * A 4-step guided wizard: Goal → Workflow → Stack → Create
 * Mirrors the deploy-wizard.tsx pattern: useReducer + ?step=N + StepIndicator.
 */
import { useEffect, useReducer } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PageTitle } from "@/components/page-title";
import { StepIndicator } from "@/components/agent-wizard/StepIndicator";
import { Step1Goal } from "@/components/agent-wizard/Step1Goal";
import { Step2Workflow } from "@/components/agent-wizard/Step2Workflow";
import { Step3Stack } from "@/components/agent-wizard/Step3Stack";
import { Step4Create } from "@/components/agent-wizard/Step4Create";
import {
  reducer,
  initialState,
  canAdvance,
  type WizardStep,
} from "@/lib/agent-wizard-state";

export default function AgentWizardPage() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  // On mount: read ?step=N from URL, clamp to the furthest reachable step,
  // and jump there so that reloading /agents/new?step=3 restores the right step.
  useEffect(() => {
    const raw = Number(searchParams.get("step"));
    if (!raw || raw === 1) return;
    // Walk forward from step 1, stopping at the first step we cannot reach.
    let target = 1 as WizardStep;
    for (let s = 2 as WizardStep; s <= 4 && s <= raw; s = (s + 1) as WizardStep) {
      if (canAdvance({ ...initialState, step: target }, s as WizardStep)) {
        target = s as WizardStep;
      } else {
        break;
      }
    }
    if (target !== 1) {
      dispatch({ type: "GOTO_STEP", step: target });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleNext() {
    const next = (state.step + 1) as WizardStep;
    if (next > 4) return;
    if (canAdvance(state, next)) {
      dispatch({ type: "GOTO_STEP", step: next });
      setSearchParams((sp) => {
        sp.set("step", String(next));
        return sp;
      });
    }
  }

  function handleBack() {
    if (state.step === 1) return;
    const prev = (state.step - 1) as WizardStep;
    dispatch({ type: "GOTO_STEP", step: prev });
    setSearchParams((sp) => {
      sp.set("step", String(prev));
      return sp;
    });
  }

  function handleJump(n: WizardStep) {
    if (!canAdvance(state, n)) return;
    dispatch({ type: "GOTO_STEP", step: n });
    setSearchParams((sp) => {
      sp.set("step", String(n));
      return sp;
    });
  }

  const StepBody = (() => {
    switch (state.step) {
      case 1:
        return <Step1Goal state={state} dispatch={dispatch} />;
      case 2:
        return <Step2Workflow state={state} dispatch={dispatch} />;
      case 3:
        return <Step3Stack state={state} dispatch={dispatch} />;
      case 4:
        return <Step4Create state={state} dispatch={dispatch} />;
    }
  })();

  // Step 4 has its own Create button; we hide the Next button there.
  const showNavFooter = state.step < 4;

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      <header className="space-y-3">
        <PageTitle>Create an agent</PageTitle>
        <StepIndicator
          current={state.step}
          canAdvanceTo={(n) => canAdvance(state, n)}
          onJump={handleJump}
        />
      </header>

      <main>{StepBody}</main>

      {showNavFooter && (
        <footer className="flex justify-between pt-3 border-t border-zinc-800">
          <button
            type="button"
            onClick={() => navigate("/agents")}
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
              data-testid="nextBtn"
              onClick={handleNext}
              disabled={!canAdvance(state, (state.step + 1) as WizardStep)}
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
