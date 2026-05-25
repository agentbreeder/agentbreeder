/**
 * Tests for GetStartedChecklist — 4-step onboarding panel.
 *
 * Mirrors the QueryClientProvider test wrapper pattern from
 * provider-catalog.test.tsx (retries disabled so errors surface immediately).
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// Mock api module — we control per-test what each list returns.
vi.mock("@/lib/api", () => ({
  api: {
    providers: {
      list: vi.fn(),
    },
    agents: {
      list: vi.fn(),
    },
    deploys: {
      list: vi.fn(),
    },
  },
}));

import { api } from "@/lib/api";
import { GetStartedChecklist } from "./get-started-checklist";

/** Build a QueryClient with retries off so errors surface immediately. */
function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function renderChecklist(client: QueryClient) {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <GetStartedChecklist />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Build a minimal ApiResponse-shaped mock. */
function mockApiResponse(total: number) {
  return Promise.resolve({ data: [], meta: { page: 1, per_page: 1, total } });
}

// Helper to set up all three mocks at once; override individual keys.
function mockApi({
  providers = 0,
  agents = 0,
  deploys = 0,
}: {
  providers?: number;
  agents?: number;
  deploys?: number;
}) {
  (api.providers.list as ReturnType<typeof vi.fn>).mockReturnValue(
    mockApiResponse(providers),
  );
  (api.agents.list as ReturnType<typeof vi.fn>).mockReturnValue(
    mockApiResponse(agents),
  );
  (api.deploys.list as ReturnType<typeof vi.fn>).mockReturnValue(
    mockApiResponse(deploys),
  );
}

describe("GetStartedChecklist", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Clear all relevant localStorage keys before each test.
    localStorage.removeItem("ag-playground-used-v1");
    localStorage.removeItem("ag-getstarted-dismissed-v1");
  });

  afterEach(() => {
    localStorage.removeItem("ag-playground-used-v1");
    localStorage.removeItem("ag-getstarted-dismissed-v1");
  });

  it("renders all four step labels", async () => {
    mockApi({});
    renderChecklist(makeClient());
    await waitFor(() => {
      expect(screen.getByTestId("step-connect-model")).toBeInTheDocument();
      expect(screen.getByTestId("step-create-agent")).toBeInTheDocument();
      expect(screen.getByTestId("step-test-playground")).toBeInTheDocument();
      expect(screen.getByTestId("step-deploy")).toBeInTheDocument();
    });
  });

  it("marks 'Connect a model' done when providers exist", async () => {
    mockApi({ providers: 1 });
    renderChecklist(makeClient());
    await waitFor(() => {
      expect(screen.getByTestId("step-connect-model")).toHaveAttribute("data-state", "done");
    });
  });

  it("marks 'Create your first agent' done when agents exist", async () => {
    mockApi({ agents: 2 });
    renderChecklist(makeClient());
    await waitFor(() => {
      expect(screen.getByTestId("step-create-agent")).toHaveAttribute("data-state", "done");
    });
  });

  it("marks 'Test it in the Playground' done when localStorage flag is set", async () => {
    localStorage.setItem("ag-playground-used-v1", "1");
    mockApi({});
    renderChecklist(makeClient());
    await waitFor(() => {
      const step = screen.getByTestId("step-test-playground");
      expect(step).toHaveAttribute("data-state", "done");
    });
  });

  it("marks 'Deploy' done when deploys exist", async () => {
    mockApi({ deploys: 3 });
    renderChecklist(makeClient());
    await waitFor(() => {
      expect(screen.getByTestId("step-deploy")).toHaveAttribute("data-state", "done");
    });
  });

  it("first non-done step is active, subsequent are locked", async () => {
    // providers done, rest not — so step 2 (create-agent) should be active.
    mockApi({ providers: 1 });
    renderChecklist(makeClient());
    await waitFor(() => {
      expect(screen.getByTestId("step-connect-model")).toHaveAttribute("data-state", "done");
      expect(screen.getByTestId("step-create-agent")).toHaveAttribute("data-state", "active");
      expect(screen.getByTestId("step-test-playground")).toHaveAttribute("data-state", "locked");
      expect(screen.getByTestId("step-deploy")).toHaveAttribute("data-state", "locked");
    });
  });

  it("renders nothing when all four signals satisfied", async () => {
    localStorage.setItem("ag-playground-used-v1", "1");
    mockApi({ providers: 1, agents: 1, deploys: 1 });
    const { container } = renderChecklist(makeClient());
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it("hides and persists dismiss flag when dismiss control is clicked", async () => {
    mockApi({});
    const { container } = renderChecklist(makeClient());
    // Wait for the panel to appear.
    const dismissBtn = await screen.findByTestId("checklist-dismiss");
    fireEvent.click(dismissBtn);
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
    expect(localStorage.getItem("ag-getstarted-dismissed-v1")).toBe("1");
  });

  it("renders nothing when dismissed flag is already set", async () => {
    localStorage.setItem("ag-getstarted-dismissed-v1", "1");
    mockApi({});
    const { container } = renderChecklist(makeClient());
    // Give queries time to settle — component should still be null.
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it("'Create your first agent' CTA links to /agents/new", async () => {
    // Step 2 active (providers done, agents not).
    mockApi({ providers: 1 });
    renderChecklist(makeClient());
    await waitFor(() => {
      const link = screen.getByTestId("cta-create-agent");
      expect(link).toHaveAttribute("href", "/agents/new");
    });
  });
});
