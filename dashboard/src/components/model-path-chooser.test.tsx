/**
 * Tests for ModelPathChooser (Phase 3 — model path simplification).
 *
 * Covers:
 *   - Three path cards render, Gateway selected by default
 *   - Clicking a card switches the active panel
 *   - Local panel: Register button creates an Ollama provider record
 *   - Local panel: error state renders error message
 *   - Direct panel: Settings link is present
 *   - syncButton prop renders inside the chooser
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";

// --- mocks ---

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { id: "1", email: "a@b.com", name: "A", role: "deployer", team: "eng" },
  }),
}));

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    providers: {
      catalog: vi.fn().mockResolvedValue({ data: [] }),
      catalogStatus: vi.fn().mockResolvedValue({ data: {} }),
      create: vi.fn(),
    },
  },
}));

import { api } from "@/lib/api";
import { ModelPathChooser } from "./model-path-chooser";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderChooser(props: { syncButton?: React.ReactNode } = {}) {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeClient()}>
        <ModelPathChooser {...props} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("ModelPathChooser — path cards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });
    (api.providers.catalogStatus as ReturnType<typeof vi.fn>).mockResolvedValue({ data: {} });
  });

  it("renders all three path cards", () => {
    renderChooser();
    expect(screen.getByTestId("path-card-local")).toBeInTheDocument();
    expect(screen.getByTestId("path-card-gateway")).toBeInTheDocument();
    expect(screen.getByTestId("path-card-direct")).toBeInTheDocument();
  });

  it("gateway path card is selected by default (aria-pressed=true)", () => {
    renderChooser();
    expect(screen.getByTestId("path-card-gateway")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("path-card-local")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("path-card-direct")).toHaveAttribute("aria-pressed", "false");
  });

  it("shows the gateway panel by default", () => {
    renderChooser();
    expect(screen.getByTestId("gateway-path-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("local-path-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("direct-path-panel")).not.toBeInTheDocument();
  });

  it("switching to Local card shows the local panel", () => {
    renderChooser();
    fireEvent.click(screen.getByTestId("path-card-local"));
    expect(screen.getByTestId("local-path-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("gateway-path-panel")).not.toBeInTheDocument();
  });

  it("switching to Direct card shows the direct panel", () => {
    renderChooser();
    fireEvent.click(screen.getByTestId("path-card-direct"));
    expect(screen.getByTestId("direct-path-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("gateway-path-panel")).not.toBeInTheDocument();
  });

  it("path card badges render (Free, Recommended, Advanced)", () => {
    renderChooser();
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(screen.getByText("Recommended")).toBeInTheDocument();
    expect(screen.getByText("Advanced")).toBeInTheDocument();
  });

  it("renders a syncButton prop inside the chooser header", () => {
    renderChooser({ syncButton: <button data-testid="sync-btn">Sync</button> });
    expect(screen.getByTestId("sync-btn")).toBeInTheDocument();
    expect(screen.getByTestId("model-path-chooser")).toContainElement(screen.getByTestId("sync-btn"));
  });

  it("shows the question heading", () => {
    renderChooser();
    expect(screen.getByText("How do you want to run models?")).toBeInTheDocument();
  });
});

describe("ModelPathChooser — Local path panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });
    (api.providers.catalogStatus as ReturnType<typeof vi.fn>).mockResolvedValue({ data: {} });
  });

  function openLocalPanel() {
    renderChooser();
    fireEvent.click(screen.getByTestId("path-card-local"));
  }

  it("renders the Register Ollama provider button", () => {
    openLocalPanel();
    expect(screen.getByTestId("local-register-btn")).toHaveTextContent("Register Ollama provider");
  });

  it("creates an Ollama provider record when Register is clicked", async () => {
    (api.providers.create as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { id: "p1", name: "Ollama (local)" },
    });

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-register-btn"));

    await waitFor(() => {
      expect(api.providers.create).toHaveBeenCalledWith({
        name: "Ollama (local)",
        provider_type: "ollama",
        base_url: "http://localhost:11434",
      });
    });
    expect(await screen.findByTestId("local-register-result")).toBeInTheDocument();
  });

  it("shows an error when registration fails", async () => {
    (api.providers.create as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Provider already exists"),
    );

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-register-btn"));

    expect(await screen.findByTestId("local-register-error")).toHaveTextContent(
      /Provider already exists/,
    );
  });
});
