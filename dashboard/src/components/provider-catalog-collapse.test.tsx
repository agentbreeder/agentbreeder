/**
 * Tests for ProviderCatalog collapseAdvanced prop (Phase 3 — model path simplification).
 *
 * When collapseAdvanced={true} the eight niche OpenAI-compatible providers
 * (cerebras, deepinfra, fireworks, groq, hyperbolic, moonshot, nvidia, together)
 * are hidden behind a "More providers (advanced)" disclosure toggle.
 * Primary providers (anything not in that set) render immediately above the toggle.
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach } from "vitest";

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
      catalog: vi.fn(),
      catalogStatus: vi.fn().mockResolvedValue({ data: {} }),
    },
  },
}));

import { api } from "@/lib/api";
import { ProviderCatalog } from "./provider-catalog";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

/** Eight niche providers that must be hidden by default when collapseAdvanced=true. */
const ADVANCED_NAMES = [
  "cerebras",
  "deepinfra",
  "fireworks",
  "groq",
  "hyperbolic",
  "moonshot",
  "nvidia",
  "together",
] as const;

function makeCatalogEntry(name: string) {
  return {
    name,
    type: "openai_compatible" as const,
    base_url: `https://${name}.example.com/v1`,
    api_key_env: `${name.toUpperCase()}_API_KEY`,
    default_headers: {},
    docs: null,
    discovery: null,
    notable_models: [],
    source: "builtin" as const,
  };
}

const MOCK_CATALOG = [
  // One non-advanced provider that should always show
  makeCatalogEntry("some-provider"),
  // All eight advanced providers
  ...ADVANCED_NAMES.map(makeCatalogEntry),
];

function renderCatalog(props: { collapseAdvanced?: boolean } = {}) {
  const client = makeClient();
  return render(
    <QueryClientProvider client={client}>
      <ProviderCatalog collapseAdvanced={props.collapseAdvanced} />
    </QueryClientProvider>,
  );
}

describe("ProviderCatalog — collapseAdvanced prop", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.providers.catalogStatus as ReturnType<typeof vi.fn>).mockResolvedValue({ data: {} });
    (api.providers.catalog as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: MOCK_CATALOG,
    });
  });

  it("renders all providers flat when collapseAdvanced is omitted", async () => {
    renderCatalog();

    // Primary provider visible
    expect(await screen.findByText("some-provider")).toBeInTheDocument();
    // Advanced providers also visible (no toggle)
    for (const name of ADVANCED_NAMES) {
      expect(await screen.findByText(name)).toBeInTheDocument();
    }
    // No disclosure toggle
    expect(screen.queryByTestId("catalog-advanced-toggle")).not.toBeInTheDocument();
  });

  it("renders all providers flat when collapseAdvanced={false}", async () => {
    renderCatalog({ collapseAdvanced: false });

    expect(await screen.findByText("some-provider")).toBeInTheDocument();
    expect(screen.queryByTestId("catalog-advanced-toggle")).not.toBeInTheDocument();
  });

  it("shows primary providers immediately when collapseAdvanced={true}", async () => {
    renderCatalog({ collapseAdvanced: true });

    expect(await screen.findByText("some-provider")).toBeInTheDocument();
  });

  it("hides advanced providers behind the toggle when collapseAdvanced={true}", async () => {
    renderCatalog({ collapseAdvanced: true });

    // Wait for the toggle to appear (catalog has loaded)
    const toggle = await screen.findByTestId("catalog-advanced-toggle");
    expect(toggle).toBeInTheDocument();

    // Advanced section should NOT be visible before clicking
    expect(screen.queryByTestId("catalog-advanced-section")).not.toBeInTheDocument();

    // None of the advanced providers should be visible yet
    for (const name of ADVANCED_NAMES) {
      expect(screen.queryByText(name)).not.toBeInTheDocument();
    }
  });

  it("shows advanced providers after clicking the toggle", async () => {
    renderCatalog({ collapseAdvanced: true });

    const toggle = await screen.findByTestId("catalog-advanced-toggle");
    fireEvent.click(toggle);

    // All advanced providers should now be visible
    await waitFor(() => {
      for (const name of ADVANCED_NAMES) {
        expect(screen.getByText(name)).toBeInTheDocument();
      }
    });

    expect(screen.getByTestId("catalog-advanced-section")).toBeInTheDocument();
  });

  it("collapses advanced providers again when toggle is clicked a second time", async () => {
    renderCatalog({ collapseAdvanced: true });

    const toggle = await screen.findByTestId("catalog-advanced-toggle");

    // Open
    fireEvent.click(toggle);
    await waitFor(() => expect(screen.getByText("groq")).toBeInTheDocument());

    // Close
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(screen.queryByText("groq")).not.toBeInTheDocument();
    });
  });

  it("toggle button has correct aria-expanded state", async () => {
    renderCatalog({ collapseAdvanced: true });

    const toggle = await screen.findByTestId("catalog-advanced-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("toggle label includes the count of advanced providers", async () => {
    renderCatalog({ collapseAdvanced: true });

    const toggle = await screen.findByTestId("catalog-advanced-toggle");
    // Should mention "8 providers" (the ADVANCED_NAMES length)
    expect(toggle).toHaveTextContent(`${ADVANCED_NAMES.length} providers`);
  });
});
