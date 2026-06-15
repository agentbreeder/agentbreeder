import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BuilderInsightsPage from "./builder-insights";

vi.mock("@/lib/api", () => ({
  api: {
    analytics: {
      funnel: vi.fn().mockResolvedValue({
        data: {
          period: "7d",
          time_to_first_deploy_p50_s: 240,
          time_to_first_deploy_p90_s: 600,
          stages: [
            { key: "builder_session_started", label: "Converse", count: 100, dropoff_pct: 0 },
            { key: "deploy_succeeded", label: "Live", count: 38, dropoff_pct: 62 },
          ],
          engines: [],
        },
        meta: {}, errors: [],
      }),
    },
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("BuilderInsightsPage", () => {
  it("renders the funnel heading and a stage label", async () => {
    render(wrap(<BuilderInsightsPage />));
    expect(await screen.findByText("Converse")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Builder/i })).toBeInTheDocument();
  });
});
