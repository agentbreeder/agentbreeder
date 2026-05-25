/**
 * Tests for ProviderCatalog graceful degradation (Phase 2 — catalog resilience).
 *
 * When the /api/v1/providers/catalog fetch fails the component must NOT render
 * a full-width blocking red banner that replaces all content. Instead it must
 * show an inline, non-blocking notice with a Retry button while keeping the
 * rest of the catalog UI structure present.
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach } from "vitest";

// Mock the auth hook so the component can render without a real AuthContext
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { id: "1", email: "a@b.com", name: "A", role: "viewer", team: "eng" } }),
}));

// Mock the toast hook (used inside ConfigureModal and other sub-components)
vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

// Mock the api module — we control whether catalog() resolves or rejects per test
vi.mock("@/lib/api", () => ({
  api: {
    providers: {
      catalog: vi.fn(),
      catalogStatus: vi.fn().mockResolvedValue({ data: {} }),
    },
  },
}));

import { api } from "@/lib/api";
import { ProviderCatalog } from "./provider-catalog";

/** Helper: build a QueryClient with retries disabled so errors surface immediately. */
function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderCatalog(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <ProviderCatalog />
    </QueryClientProvider>
  );
}

describe("ProviderCatalog — graceful catalog error degradation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // catalogStatus always succeeds in these tests
    (api.providers.catalogStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {},
    });
  });

  it("shows a Retry button when the catalog fetch fails", async () => {
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Network error")
    );

    renderCatalog(makeClient());

    // Wait for the retry affordance to appear
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("does NOT render the old blocking red 'Failed to load catalog' banner", async () => {
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Network error")
    );

    renderCatalog(makeClient());

    await screen.findByRole("button", { name: /retry/i });
    expect(screen.queryByText(/Failed to load catalog/i)).not.toBeInTheDocument();
  });

  it("calls refetch when the Retry button is clicked", async () => {
    // First call rejects, second call returns empty list (so we can assert it was called)
    (api.providers.catalog as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({ data: [] });

    renderCatalog(makeClient());

    const retryBtn = await screen.findByRole("button", { name: /retry/i });
    fireEvent.click(retryBtn);

    // After retry the catalog query refetches — catalog() should have been called twice
    await waitFor(() => {
      expect(api.providers.catalog).toHaveBeenCalledTimes(2);
    });
  });

  it("shows empty-state copy when catalog resolves to an empty list (no error)", async () => {
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: [],
    });

    renderCatalog(makeClient());

    expect(
      await screen.findByText("No providers in the catalog yet.")
    ).toBeInTheDocument();
    // No retry button when there is no error
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("shows 'retry above' copy in the empty-state when catalog fetch fails", async () => {
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Network error")
    );

    renderCatalog(makeClient());

    expect(
      await screen.findByText("Provider list unavailable — retry above.")
    ).toBeInTheDocument();
  });

  it("renders normally when the catalog fetch succeeds", async () => {
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: [
        {
          name: "nvidia",
          type: "openai_compatible",
          base_url: "https://integrate.api.nvidia.com/v1",
          api_key_env: "NVIDIA_API_KEY",
          default_headers: {},
          docs: null,
          discovery: null,
          notable_models: [],
          source: "builtin",
        },
      ],
    });

    renderCatalog(makeClient());

    // Provider name should be rendered (CatalogRow uses preset.name) — wait for it
    expect(await screen.findByText("nvidia")).toBeInTheDocument();
    // No retry button when successful
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });
});
