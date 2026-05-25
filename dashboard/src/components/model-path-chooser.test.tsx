/**
 * Tests for ModelPathChooser (Phase 3 — model path simplification).
 *
 * Covers:
 *   - Three path cards render, Gateway selected by default
 *   - Clicking a card switches the active panel
 *   - Local panel: Detect button calls detectOllama; success shows discovered models
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
      detectOllama: vi.fn(),
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

  it("renders Detect Ollama button", () => {
    openLocalPanel();
    expect(screen.getByTestId("local-detect-btn")).toBeInTheDocument();
    expect(screen.getByTestId("local-detect-btn")).toHaveTextContent("Detect Ollama");
  });

  it("calls detectOllama when Detect button is clicked", async () => {
    (api.providers.detectOllama as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { provider: { id: "p1", name: "Ollama (local)" }, models: [], created: true },
    });

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-detect-btn"));

    await waitFor(() => {
      expect(api.providers.detectOllama).toHaveBeenCalledTimes(1);
    });
  });

  it("shows discovered model names after successful detection", async () => {
    (api.providers.detectOllama as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        provider: { id: "p1", name: "Ollama (local)" },
        models: [
          { id: "llama3.2", name: "llama3.2", context_window: null, max_output_tokens: null, input_price_per_million: null, output_price_per_million: null, capabilities: [] },
          { id: "mistral", name: "mistral", context_window: null, max_output_tokens: null, input_price_per_million: null, output_price_per_million: null, capabilities: [] },
        ],
        created: true,
      },
    });

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-detect-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("local-detect-result")).toBeInTheDocument();
    });

    expect(screen.getByText("llama3.2")).toBeInTheDocument();
    expect(screen.getByText("mistral")).toBeInTheDocument();
  });

  it("shows success message indicating provider was newly created", async () => {
    (api.providers.detectOllama as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        provider: { id: "p1", name: "Ollama (local)" },
        models: [],
        created: true,
      },
    });

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-detect-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("local-detect-result")).toBeInTheDocument();
    });

    expect(screen.getByText(/registered as a new provider/i)).toBeInTheDocument();
  });

  it("shows success message indicating existing provider was refreshed", async () => {
    (api.providers.detectOllama as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        provider: { id: "p1", name: "Ollama (local)" },
        models: [],
        created: false,
      },
    });

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-detect-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("local-detect-result")).toBeInTheDocument();
    });

    expect(screen.getByText(/models refreshed/i)).toBeInTheDocument();
  });

  it("shows error message when detection fails", async () => {
    (api.providers.detectOllama as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Connection refused"),
    );

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-detect-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("local-detect-error")).toBeInTheDocument();
    });

    expect(screen.getByText(/Connection refused/i)).toBeInTheDocument();
  });

  it("Run again button resets result state back to detect form", async () => {
    (api.providers.detectOllama as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { provider: { id: "p1", name: "Ollama (local)" }, models: [], created: true },
    });

    openLocalPanel();
    fireEvent.click(screen.getByTestId("local-detect-btn"));

    await waitFor(() => screen.getByTestId("local-detect-result"));

    fireEvent.click(screen.getByTestId("local-detect-reset"));

    // Back to the detect form — button should be visible again
    expect(screen.getByTestId("local-detect-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("local-detect-result")).not.toBeInTheDocument();
  });
});

describe("ModelPathChooser — Direct path panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });
    (api.providers.catalogStatus as ReturnType<typeof vi.fn>).mockResolvedValue({ data: {} });
  });

  it("shows Settings link in the foundation-providers pointer", () => {
    renderChooser();
    fireEvent.click(screen.getByTestId("path-card-direct"));

    const link = screen.getByTestId("direct-settings-link");
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/settings");
  });

  it("contains copy mentioning OpenAI, Anthropic, or Google", () => {
    renderChooser();
    fireEvent.click(screen.getByTestId("path-card-direct"));

    expect(screen.getByText(/openai, anthropic, or google/i)).toBeInTheDocument();
  });
});
