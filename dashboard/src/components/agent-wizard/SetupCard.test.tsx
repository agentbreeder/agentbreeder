import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SetupCard } from "./SetupCard";
import { api } from "@/lib/api";

function renderCard(props: Parameters<typeof SetupCard>[0]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SetupCard {...props} />
    </QueryClientProvider>,
  );
}

describe("SetupCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("secret kind stores via api.secrets.create and confirms with the name", async () => {
    const create = vi
      .spyOn(api.secrets, "create")
      .mockResolvedValue({ data: { name: "ZENDESK_API_KEY", masked_value: "••••" } } as never);
    const onComplete = vi.fn();

    renderCard({
      request: { kind: "secret", name: "ZENDESK_API_KEY", reason: "read tickets" },
      onComplete,
      onSkip: vi.fn(),
    });

    expect(screen.getByText(/read tickets/i)).toBeInTheDocument();
    const input = screen.getByTestId("setup-secret-input") as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: "sk-zendesk-123" } });
    fireEvent.click(screen.getByTestId("setup-card-submit"));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ name: "ZENDESK_API_KEY", value: "sk-zendesk-123" }),
    );
    await waitFor(() =>
      expect(onComplete).toHaveBeenCalledWith(expect.stringContaining("ZENDESK_API_KEY")),
    );
    expect(input.value).toBe("");
  });

  it("provider kind stores a provider-key secret and confirms", async () => {
    const create = vi
      .spyOn(api.secrets, "create")
      .mockResolvedValue({ data: { name: "openai/api-key", masked_value: "••••" } } as never);
    const onComplete = vi.fn();

    renderCard({
      request: { kind: "provider", name: "openai" },
      onComplete,
      onSkip: vi.fn(),
    });

    fireEvent.change(screen.getByTestId("setup-secret-input"), {
      target: { value: "sk-openai-xyz" },
    });
    fireEvent.click(screen.getByTestId("setup-card-submit"));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ name: "openai/api-key", value: "sk-openai-xyz" }),
    );
    await waitFor(() =>
      expect(onComplete).toHaveBeenCalledWith(expect.stringContaining("openai")),
    );
  });

  it("mcp kind registers + discovers and confirms with the tool count", async () => {
    const create = vi
      .spyOn(api.mcpServers, "create")
      .mockResolvedValue({ data: { id: "m1", name: "zendesk" } } as never);
    const discover = vi
      .spyOn(api.mcpServers, "discover")
      .mockResolvedValue({ data: { tools: [{ name: "a" }, { name: "b" }], total: 2 } } as never);
    const onComplete = vi.fn();

    renderCard({
      request: { kind: "mcp", name: "zendesk" },
      onComplete,
      onSkip: vi.fn(),
    });

    fireEvent.change(screen.getByTestId("setup-mcp-endpoint"), {
      target: { value: "https://mcp.zendesk.example/sse" },
    });
    fireEvent.click(screen.getByTestId("setup-card-submit"));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ name: "zendesk", endpoint: "https://mcp.zendesk.example/sse" }),
      ),
    );
    await waitFor(() => expect(discover).toHaveBeenCalledWith("m1"));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(expect.stringContaining("2 tools")));
  });

  it("surfaces an error and does not complete when the mutation fails", async () => {
    vi.spyOn(api.secrets, "create").mockRejectedValue(new Error("backend down"));
    const onComplete = vi.fn();

    renderCard({
      request: { kind: "secret", name: "ZENDESK_API_KEY" },
      onComplete,
      onSkip: vi.fn(),
    });

    fireEvent.change(screen.getByTestId("setup-secret-input"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("setup-card-submit"));

    await waitFor(() => expect(screen.getByText(/backend down/i)).toBeInTheDocument());
    expect(onComplete).not.toHaveBeenCalled();
  });
});
