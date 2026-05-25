/**
 * Tests for ChatBuildPanel — BYO-key conversational agent builder.
 *
 * All API calls are mocked. No real network calls, no real API key.
 */
// React import omitted — JSX transform handles it automatically
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import { ChatBuildPanel } from "./ChatBuildPanel";
import type { SecretSummary, ChatBuildResult, ApiResponse, ApiMeta } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mock the API module
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", () => ({
  api: {
    secrets: {
      list: vi.fn(),
      create: vi.fn(),
    },
    builders: {
      chat: vi.fn(),
    },
    agents: {
      fromYaml: vi.fn(),
    },
  },
}));

// Mock react-router-dom navigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...(actual as object),
    useNavigate: () => mockNavigate,
  };
});

// Mock useAuth — returns a stable fake user so the per-user secret name is deterministic.
const FAKE_USER_ID = "00000000-0000-0000-0000-000000000001";
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: {
      id: FAKE_USER_ID,
      email: "test@example.com",
      name: "Test User",
      role: "admin",
      team: "engineering",
    },
    token: "fake-token",
    isLoading: false,
    login: vi.fn(),
    register: vi.fn(),
    changePassword: vi.fn(),
    logout: vi.fn(),
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Per-user secret name — must match builderKeySecretName(FAKE_USER_ID) in ChatBuildPanel.tsx
const BUILDER_KEY_SECRET = `AGENTBREEDER_CLAUDE_BUILDER_KEY__${FAKE_USER_ID}`;

function makeSummary(name: string): SecretSummary {
  return {
    name,
    backend: "env",
    workspace: "default",
    updated_at: "2026-01-01T00:00:00Z",
    masked_value: "sk-ant-***",
    mirror_destinations: [],
  };
}

const EMPTY_META: ApiMeta = { page: 1, per_page: 100, total: 0 };

function apiResp<T>(data: T): ApiResponse<T> {
  return { data, meta: EMPTY_META, errors: [] };
}

function noKeyResponse() {
  return Promise.resolve(apiResp([] as SecretSummary[]));
}

function withKeyResponse() {
  return Promise.resolve(apiResp([makeSummary(BUILDER_KEY_SECRET)]));
}

function chatResp(result: ChatBuildResult): ApiResponse<ChatBuildResult> {
  return apiResp(result);
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChatBuildPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests: Key-entry guard
// ---------------------------------------------------------------------------

describe("ChatBuildPanel — key guard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
  });

  it("shows key-entry form when key is not stored", async () => {
    vi.mocked(api.secrets.list).mockImplementation(noKeyResponse);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("key-input")).toBeInTheDocument();
    });
    expect(screen.getByTestId("store-key-btn")).toBeInTheDocument();
    // Chat input must NOT be visible
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
  });

  it("does not show chat until the key is stored", async () => {
    vi.mocked(api.secrets.list).mockImplementation(noKeyResponse);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("key-input")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("send-btn")).not.toBeInTheDocument();
  });

  it("calls secrets.create with the key name and value", async () => {
    vi.mocked(api.secrets.list).mockImplementation(noKeyResponse);
    vi.mocked(api.secrets.create).mockResolvedValue(
      apiResp(makeSummary(BUILDER_KEY_SECRET)),
    );

    renderPanel();

    await waitFor(() => expect(screen.getByTestId("key-input")).toBeInTheDocument());

    const input = screen.getByTestId("key-input");
    fireEvent.change(input, { target: { value: "sk-ant-test-key" } });
    fireEvent.click(screen.getByTestId("store-key-btn"));

    await waitFor(() => {
      expect(api.secrets.create).toHaveBeenCalledWith({
        name: BUILDER_KEY_SECRET,
        value: "sk-ant-test-key",
      });
    });
  });

  it("reveals chat after key is stored", async () => {
    vi.mocked(api.secrets.list).mockImplementation(noKeyResponse);
    vi.mocked(api.secrets.create).mockResolvedValue(
      apiResp(makeSummary(BUILDER_KEY_SECRET)),
    );

    renderPanel();

    await waitFor(() => expect(screen.getByTestId("key-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("key-input"), {
      target: { value: "sk-ant-test-key" },
    });
    fireEvent.click(screen.getByTestId("store-key-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("chat-input")).toBeInTheDocument();
    });
  });

  it("shows the chat when the key is already present in secrets list", async () => {
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("chat-input")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("key-input")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tests: Chat interaction
// ---------------------------------------------------------------------------

describe("ChatBuildPanel — chat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    // Key is always present
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
  });

  it("sends a message and shows the assistant reply", async () => {
    const chatResult: ChatBuildResult = {
      assistant_message: "What framework would you like?",
      agent_yaml: null,
      valid: false,
      errors: [],
    };
    vi.mocked(api.builders.chat).mockResolvedValue(chatResp(chatResult));

    renderPanel();

    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), {
      target: { value: "I want a support agent" },
    });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(screen.getByText("What framework would you like?")).toBeInTheDocument();
    });
  });

  it("calls builders.chat with the correct message history", async () => {
    vi.mocked(api.builders.chat).mockResolvedValue(
      chatResp({ assistant_message: "Got it!", agent_yaml: null, valid: false, errors: [] }),
    );

    renderPanel();

    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), {
      target: { value: "Build a data agent" },
    });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(api.builders.chat).toHaveBeenCalledWith([
        { role: "user", content: "Build a data agent" },
      ]);
    });
  });

  it("clears input after sending", async () => {
    vi.mocked(api.builders.chat).mockResolvedValue(
      chatResp({ assistant_message: "Ok!", agent_yaml: null, valid: false, errors: [] }),
    );

    renderPanel();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    const textarea = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Hello" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(textarea.value).toBe("");
    });
  });
});

// ---------------------------------------------------------------------------
// Tests: Valid agent spec response
// ---------------------------------------------------------------------------

describe("ChatBuildPanel — valid spec", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
  });

  const VALID_YAML =
    "name: my-agent\nversion: 1.0.0\nteam: engineering\n" +
    "owner: alice@example.com\nframework: langgraph\n" +
    "model:\n  primary: claude-sonnet-4-6\ndeploy:\n  cloud: aws\n";

  it("shows 'Create agent' button when spec is valid", async () => {
    vi.mocked(api.builders.chat).mockResolvedValue(
      chatResp({ assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] }),
    );

    renderPanel();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("create-agent-btn")).toBeInTheDocument();
    });
    expect(screen.getByTestId("spec-ready-card")).toBeInTheDocument();
  });

  it("calls agents.fromYaml and navigates on create", async () => {
    vi.mocked(api.builders.chat).mockResolvedValue(
      chatResp({ assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] }),
    );
    vi.mocked(api.agents.fromYaml).mockResolvedValue(
      apiResp({ id: "agent-123", name: "my-agent" } as never),
    );

    renderPanel();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => expect(screen.getByTestId("create-agent-btn")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("create-agent-btn"));

    await waitFor(() => {
      expect(api.agents.fromYaml).toHaveBeenCalledWith(VALID_YAML);
      expect(mockNavigate).toHaveBeenCalledWith("/agents/agent-123");
    });
  });
});

// ---------------------------------------------------------------------------
// Tests: Invalid agent spec response
// ---------------------------------------------------------------------------

describe("ChatBuildPanel — invalid spec", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
  });

  it("shows errors and no 'Create agent' button when spec is invalid", async () => {
    vi.mocked(api.builders.chat).mockResolvedValue(
      chatResp({
        assistant_message: "",
        agent_yaml: "name: bad",
        valid: false,
        errors: ["team: 'team' is a required property"],
      }),
    );

    renderPanel();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("spec-invalid-card")).toBeInTheDocument();
    });

    // Create button must NOT appear for an invalid spec
    expect(screen.queryByTestId("create-agent-btn")).not.toBeInTheDocument();
    expect(screen.getByText(/is a required property/i)).toBeInTheDocument();
  });
});
