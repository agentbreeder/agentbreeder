import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Step5Deploy } from "@/components/deploy-wizard/Step5Deploy";
import {
  initialState,
  type DeployWizardState,
} from "@/lib/deploy-wizard-state";

vi.mock("@/lib/api", () => ({
  api: {
    deployments: {
      getJob: vi.fn().mockResolvedValue({ data: { status: "provisioning" } }),
      destroyPartial: vi.fn().mockResolvedValue({ data: { status: "rollback_started" } }),
    },
  },
}));

// Mock useDeployStream so we can control SSE behaviour in tests.
let capturedOnEvent: ((e: unknown) => void) | undefined;
vi.mock("@/hooks/useDeployStream", () => ({
  useDeployStream: (_id: string | null, opts: { onEvent?: (e: unknown) => void }) => {
    capturedOnEvent = opts.onEvent;
    return { status: "open" };
  },
}));

beforeEach(() => {
  capturedOnEvent = undefined;
});

function state(extra: Partial<DeployWizardState> = {}): DeployWizardState {
  return {
    ...initialState,
    step: 5,
    jobId: "j-1",
    jobStatus: "provisioning",
    ...extra,
  };
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("Step5Deploy", () => {
  it("renders the 6-phase indicator", () => {
    wrap(<Step5Deploy state={state()} dispatch={() => {}} />);
    for (const p of ["provisioning", "building", "pushing", "deploying", "health_checking", "registering"]) {
      expect(screen.getByText(new RegExp(p, "i"))).toBeInTheDocument();
    }
  });

  it("highlights the current phase based on jobStatus", () => {
    wrap(<Step5Deploy state={state({ jobStatus: "building" })} dispatch={() => {}} />);
    const buildingNode = screen.getByText(/building/i);
    expect(buildingNode.className).toMatch(/emerald/);
  });

  it("dispatches SSE_EVENT when a stream event arrives", () => {
    const dispatch = vi.fn();
    wrap(<Step5Deploy state={state()} dispatch={dispatch} />);
    const evt = {
      type: "log", job_id: "j-1", timestamp: "",
      phase: null, step: null, total: null,
      message: "creating VPC", level: "info",
      endpoint_url: null, error_code: null,
    };
    capturedOnEvent?.(evt);
    expect(dispatch).toHaveBeenCalledWith({ type: "SSE_EVENT", event: evt });
  });

  it("on completed status shows endpoint URL + copy button", () => {
    wrap(
      <Step5Deploy
        state={state({
          jobStatus: "completed",
          endpointUrl: "https://demo-xxx-uc.a.run.app",
        })}
        dispatch={() => {}}
      />,
    );
    expect(screen.getByText("https://demo-xxx-uc.a.run.app")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("on failed status shows Roll back + Start over buttons", () => {
    const dispatch = vi.fn();
    wrap(<Step5Deploy state={state({ jobStatus: "failed" })} dispatch={dispatch} />);
    expect(screen.getByRole("button", { name: /Roll back/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Start over/i }));
    expect(dispatch).toHaveBeenCalledWith({ type: "RESET" });
  });

  it("on pending_approval shows waiting message", () => {
    wrap(
      <Step5Deploy
        state={state({ jobStatus: "pending_approval", approvalPending: true })}
        dispatch={() => {}}
      />,
    );
    expect(screen.getByText(/Awaiting admin approval/i)).toBeInTheDocument();
  });
});
