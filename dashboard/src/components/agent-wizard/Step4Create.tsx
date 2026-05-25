import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { api } from "@/lib/api";
import { formDataToYaml } from "@/lib/agent-yaml-emit";
import {
  recommendationToFormData,
  canCreate,
  SLUG_RE,
  EMAIL_RE,
  type AgentWizardState,
  type AgentWizardAction,
} from "@/lib/agent-wizard-state";
import { useEffect } from "react";

interface Props {
  state: AgentWizardState;
  dispatch: React.Dispatch<AgentWizardAction>;
}

export function Step4Create({ state, dispatch }: Props) {
  const navigate = useNavigate();
  const { user } = useAuth();

  // Prefill owner from current user's email on first render
  useEffect(() => {
    if (!state.owner && user?.email) {
      dispatch({ type: "SET_FIELD", field: "owner", value: user.email });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const nameInvalid = state.name.trim() !== "" && !SLUG_RE.test(state.name.trim());
  const ownerInvalid = state.owner.trim() !== "" && !EMAIL_RE.test(state.owner.trim());

  const createMutation = useMutation({
    mutationFn: async () => {
      const formData = recommendationToFormData(state);
      const yaml = formDataToYaml(formData);

      // Validate first
      const validationRes = await api.agents.validate(yaml);
      const validation = validationRes.data;
      if (!validation.valid) {
        throw new ValidationError(
          validation.errors.map((e) => `${e.path}: ${e.message}`).join("\n"),
        );
      }

      // Create via from-yaml
      const createdRes = await api.agents.fromYaml(yaml);
      return createdRes.data;
    },
    onSuccess: (created) => {
      navigate(`/agents/${created.id}`);
    },
  });

  // Separate validation errors from server errors
  const isValidationError = createMutation.error instanceof ValidationError;
  const validationMessage = isValidationError
    ? (createMutation.error as ValidationError).message
    : null;
  const serverError =
    createMutation.isError && !isValidationError ? createMutation.error : null;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label htmlFor="name" className="block text-sm font-medium">
            Agent name{" "}
            <span className="text-xs text-zinc-500 font-normal">slug format</span>
          </label>
          <input
            id="name"
            data-testid="agentName"
            type="text"
            value={state.name}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "name", value: e.target.value })
            }
            placeholder="my-support-agent"
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500 aria-invalid:border-red-500"
            aria-invalid={nameInvalid}
          />
          {nameInvalid && (
            <p className="text-xs text-red-400">
              Use lowercase letters, numbers, and hyphens only (e.g. my-agent)
            </p>
          )}
          <p className="text-xs text-zinc-500">
            Pattern: lowercase letters, numbers, hyphens — no spaces
          </p>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="version" className="block text-sm font-medium">
            Version
          </label>
          <input
            id="version"
            data-testid="version"
            type="text"
            value={state.version}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "version", value: e.target.value })
            }
            placeholder="1.0.0"
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="team" className="block text-sm font-medium">
            Team
          </label>
          <input
            id="team"
            data-testid="team"
            type="text"
            value={state.team}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "team", value: e.target.value })
            }
            placeholder="engineering"
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="owner" className="block text-sm font-medium">
            Owner email
          </label>
          <input
            id="owner"
            data-testid="owner"
            type="email"
            value={state.owner}
            onChange={(e) =>
              dispatch({ type: "SET_FIELD", field: "owner", value: e.target.value })
            }
            placeholder="you@example.com"
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500 aria-invalid:border-red-500"
            aria-invalid={ownerInvalid}
          />
          {ownerInvalid && (
            <p className="text-xs text-red-400">Enter a valid email address</p>
          )}
        </div>
      </div>

      {/* Validation errors */}
      {validationMessage && (
        <div
          data-testid="validation-errors"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300 whitespace-pre-wrap"
        >
          <p className="font-medium mb-1">YAML validation errors</p>
          {validationMessage}
        </div>
      )}

      {/* Server errors */}
      {serverError && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {String(serverError)}
        </div>
      )}

      <button
        type="button"
        data-testid="createAgent"
        onClick={() => createMutation.mutate()}
        disabled={!canCreate(state) || createMutation.isPending}
        className="w-full rounded-md bg-emerald-500 py-2 text-sm font-medium text-black hover:bg-emerald-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {createMutation.isPending ? "Creating…" : "Create agent →"}
      </button>
    </div>
  );
}

// Typed error class to distinguish validation from network/server errors.
class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}
