/**
 * Tests for ChatBuildPanel — BYO-key conversational agent builder.
 *
 * All API calls are mocked. No real network calls, no real API key.
 */
// React import omitted — JSX transform handles it automatically
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import { ChatBuildPanel } from "./ChatBuildPanel";
import type { SecretSummary, ApiResponse, ApiMeta } from "@/lib/api";

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
      chatStream: vi.fn(),
    },
    agents: {
      fromYaml: vi.fn(),
    },
    deploys: {
      create: vi.fn(),
      getDetail: vi.fn(),
    },
    builderSessions: {
      create: vi.fn(),
      get: vi.fn(),
      eject: vi.fn(),
      deploy: vi.fn(),
    },
    mcpServers: {
      create: vi.fn(),
      discover: vi.fn(),
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

function renderPanel(initialPrompt?: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChatBuildPanel initialPrompt={initialPrompt} />
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
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", {
        assistant_message: "What framework would you like?",
        agent_yaml: null,
        valid: false,
        errors: [],
      });
    });

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

  it("calls builders.chatStream with the correct message history", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "Got it!", agent_yaml: null, valid: false, errors: [] });
    });

    renderPanel();

    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), {
      target: { value: "Build a data agent" },
    });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(api.builders.chatStream).toHaveBeenCalledWith(
        [{ role: "user", content: "Build a data agent" }],
        expect.any(Function),
      );
    });
  });

  it("clears input after sending", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "Ok!", agent_yaml: null, valid: false, errors: [] });
    });

    renderPanel();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    const textarea = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Hello" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => {
      expect(textarea.value).toBe("");
    });
  });

  it("renders an inline setup card and continues the thread after it is completed", async () => {
    // First turn emits a setup_request (secret); second turn (after the card is
    // satisfied) returns a plain reply. The confirmation message must be threaded
    // into the second chatStream call's history.
    let call = 0;
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      call += 1;
      if (call === 1) {
        onEvent("setup_request", { kind: "secret", name: "ZENDESK_API_KEY", reason: "read tickets" });
        onEvent("done", { assistant_message: "", agent_yaml: null, valid: false, errors: [] });
      } else {
        onEvent("done", { assistant_message: "Great, all set!", agent_yaml: null, valid: false, errors: [] });
      }
    });
    vi.mocked(api.secrets.create).mockResolvedValue(apiResp(makeSummary("ZENDESK_API_KEY")));

    renderPanel();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("chat-input"), {
      target: { value: "a support agent that reads Zendesk" },
    });
    fireEvent.click(screen.getByTestId("send-btn"));

    // The inline setup card appears.
    const card = await screen.findByTestId("setup-card");
    expect(within(card).getByText(/read tickets/i)).toBeInTheDocument();

    // Provide the secret and connect.
    fireEvent.change(within(card).getByTestId("setup-secret-input"), {
      target: { value: "sk-zendesk-abc" },
    });
    fireEvent.click(within(card).getByTestId("setup-card-submit"));

    await waitFor(() =>
      expect(api.secrets.create).toHaveBeenCalledWith({
        name: "ZENDESK_API_KEY",
        value: "sk-zendesk-abc",
      }),
    );

    // The conversation continues: a 2nd chatStream call whose history carries the
    // confirmation user message referencing the secret name.
    await waitFor(() => {
      const secondCall = vi
        .mocked(api.builders.chatStream)
        .mock.calls.find((c) => {
          const msgs = c[0] as { role: string; content: string }[];
          return msgs.some((m) => m.content.includes("ZENDESK_API_KEY"));
        });
      expect(secondCall).toBeDefined();
    });

    // The card is dismissed once satisfied.
    await waitFor(() => expect(screen.queryByTestId("setup-card")).not.toBeInTheDocument());
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
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });

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
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });
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
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", {
        assistant_message: "",
        agent_yaml: "name: bad",
        valid: false,
        errors: ["team: 'team' is a required property"],
      });
    });

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

// ---------------------------------------------------------------------------
// Tests: Deploy from chat
// ---------------------------------------------------------------------------

describe("ChatBuildPanel — deploy from chat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
  });

  it("deploys the built agent and tails logs to the thread", async () => {
    vi.spyOn(api.builders, "chatStream").mockImplementation(async (_m, onEvent) => {
      onEvent("done", {
        assistant_message: "",
        agent_yaml: "name: my-agent\nversion: 1.0.0\n",
        valid: true,
        errors: [],
      });
    });
    vi.spyOn(api.deploys, "create").mockResolvedValue({
      data: { id: "job1", status: "parsing" }, meta: { page: 1, per_page: 1, total: 1 }, errors: [],
    } as never);
    const getDetailSpy = vi
      .spyOn(api.deploys, "getDetail")
      .mockResolvedValue({
        data: {
          id: "job1", agent_id: "a1", agent_name: "my-agent", status: "completed",
          target: "local", error_message: null, started_at: "", completed_at: "",
          logs: [{ timestamp: "t1", level: "info", message: "Building image…", step: null }],
        },
        meta: { page: 1, per_page: 1, total: 1 }, errors: [],
      } as never);

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    const card = await screen.findByTestId("spec-ready-card");
    fireEvent.click(within(card).getByTestId("deploy-agent-btn"));

    await waitFor(() => expect(getDetailSpy).toHaveBeenCalledWith("job1"));
    await waitFor(() => expect(screen.getByText(/Building image/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("link", { name: /Agent deployed/i })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Agent deployed/i })).toHaveAttribute("href", "/agents/a1");
  });
});

// ---------------------------------------------------------------------------
// Tests: Streaming
// ---------------------------------------------------------------------------

describe("ChatBuildPanel streaming", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
  });

  it("renders streamed tokens incrementally then the done reply", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("token", { text: "Hel" });
      onEvent("token", { text: "lo" });
      onEvent("done", {
        assistant_message: "Hello",
        agent_yaml: null,
        valid: false,
        errors: [],
      });
    });

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => expect(screen.getByText("Hello")).toBeInTheDocument());
  });

  it("accumulates token text during streaming and clears it on done", async () => {
    let capturedOnEvent: ((event: string, data: unknown) => void) | undefined;

    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      capturedOnEvent = onEvent;
      // Simulate async streaming — resolve without calling done yet
    });

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "stream me" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    // Simulate token events arriving
    await waitFor(() => expect(capturedOnEvent).toBeDefined());
    capturedOnEvent!("token", { text: "Part" });
    capturedOnEvent!("token", { text: " one" });

    await waitFor(() => expect(screen.getByText("Part one")).toBeInTheDocument());

    capturedOnEvent!("done", {
      assistant_message: "Part one done",
      agent_yaml: null,
      valid: false,
      errors: [],
    });

    // After done, the final assistant message replaces the streaming bubble
    await waitFor(() => expect(screen.getByText("Part one done")).toBeInTheDocument());
  });

  it("shows error banner when chatStream emits an error event", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("error", { detail: "Builder API key missing." });
    });

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "oops" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() =>
      expect(screen.getByText("Builder API key missing.")).toBeInTheDocument(),
    );
  });
});

// ---------------------------------------------------------------------------
// Tests: initialPrompt auto-send
// ---------------------------------------------------------------------------

describe("ChatBuildPanel initialPrompt", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
  });

  it("auto-sends initialPrompt and shows user bubble when key is connected", async () => {
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", {
        assistant_message: "Got your request!",
        agent_yaml: null,
        valid: false,
        errors: [],
      });
    });

    renderPanel("hello world");

    // User bubble appears automatically
    await waitFor(() => {
      expect(screen.getByText("hello world")).toBeInTheDocument();
    });
    // chatStream was called exactly once (auto-send)
    await waitFor(() => {
      expect(api.builders.chatStream).toHaveBeenCalledTimes(1);
    });
    expect(api.builders.chatStream).toHaveBeenCalledWith(
      [{ role: "user", content: "hello world" }],
      expect.any(Function),
    );
  });

  it("does NOT auto-call chatStream on mount when no initialPrompt is given", async () => {
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "reply", agent_yaml: null, valid: false, errors: [] });
    });

    renderPanel(); // no initialPrompt

    // Wait for chat UI to be ready
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeInTheDocument());

    // chatStream should NOT have been called
    expect(api.builders.chatStream).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Tests: Eject to code (Wave 3)
// ---------------------------------------------------------------------------

describe("ChatBuildPanel — eject to code", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    vi.mocked(api.secrets.list).mockImplementation(withKeyResponse);
  });

  const VALID_YAML =
    "name: my-agent\nversion: 1.0.0\nteam: engineering\n" +
    "owner: alice@example.com\nframework: langgraph\n" +
    "model:\n  primary: claude-sonnet-4-6\ndeploy:\n  cloud: aws\n";

  it("ejects the validated spec to code and shows generated files in the Code tab", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });
    vi.mocked(api.builderSessions.create).mockResolvedValue(
      apiResp({
        id: "sess-1",
        team: "engineering",
        engine: "claude",
        agent_yaml: VALID_YAML,
        files: {},
        deploy_job_id: null,
        history: [],
      } as never),
    );
    vi.mocked(api.builderSessions.eject).mockImplementation(
      async (_id: string, _instruction: string, onEvent: (e: string, d: unknown) => void) => {
        onEvent("file_change", { path: "agent.py", diff: "+x", content: "print('hi')\n" });
        onEvent("complete", { summary: "done" });
      },
    );

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    // Validated spec appears with the Eject-to-code action.
    const ejectBtn = await screen.findByTestId("eject-code-btn");
    fireEvent.click(ejectBtn);

    // A session is lazily created, then the eject stream runs.
    await waitFor(() => expect(api.builderSessions.create).toHaveBeenCalledWith("claude"));
    await waitFor(() =>
      expect(api.builderSessions.eject).toHaveBeenCalledWith(
        "sess-1",
        expect.any(String),
        expect.any(Function),
      ),
    );

    // On "complete" the panel auto-switches to the Code tab; the file is shown.
    await waitFor(() => expect(screen.getByText("agent.py")).toBeInTheDocument());

    // Explicitly clicking the Code tab keeps the file visible (CodeArtifactPanel rendered).
    fireEvent.click(screen.getByTestId("artifact-tab-code"));
    expect(screen.getByText("agent.py")).toBeInTheDocument();
  });

  it("emits the eject funnel analytics events", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });
    vi.mocked(api.builderSessions.create).mockResolvedValue(
      apiResp({
        id: "sess-analytics",
        team: "engineering",
        engine: "claude",
        agent_yaml: VALID_YAML,
        files: {},
        deploy_job_id: null,
        history: [],
      } as never),
    );
    vi.mocked(api.builderSessions.eject).mockImplementation(
      async (_id: string, _instruction: string, onEvent: (e: string, d: unknown) => void) => {
        onEvent("file_change", { path: "agent.py", diff: "+x", content: "print('hi')\n" });
        onEvent("complete", { summary: "done" });
      },
    );

    // Spy on the CustomEvent dispatch that track() uses.
    const events: { event: string; props: Record<string, unknown> }[] = [];
    const dispatchSpy = vi
      .spyOn(window, "dispatchEvent")
      .mockImplementation((e: Event) => {
        if (e instanceof CustomEvent && e.type === "agentbreeder:analytics") {
          events.push(e.detail);
        }
        return true;
      });

    try {
      renderPanel();
      fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
      fireEvent.click(screen.getByTestId("send-btn"));

      const ejectBtn = await screen.findByTestId("eject-code-btn");
      fireEvent.click(ejectBtn);

      await waitFor(() =>
        expect(events.map((e) => e.event)).toEqual(
          expect.arrayContaining([
            "eject_to_code_started",
            "coding_agent_turn",
            "eject_to_code_completed",
          ]),
        ),
      );
      const started = events.find((e) => e.event === "eject_to_code_started");
      expect(started?.props).toMatchObject({ engine: "claude" });
      const turn = events.find((e) => e.event === "coding_agent_turn");
      expect(turn?.props).toMatchObject({ path: "agent.py" });
    } finally {
      dispatchSpy.mockRestore();
    }
  });

  it("surfaces an error when eject emits an error event", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });
    vi.mocked(api.builderSessions.create).mockResolvedValue(
      apiResp({
        id: "sess-err",
        team: "engineering",
        engine: "claude",
        agent_yaml: VALID_YAML,
        files: {},
        deploy_job_id: null,
        history: [],
      } as never),
    );
    vi.mocked(api.builderSessions.eject).mockImplementation(
      async (_id: string, _instruction: string, onEvent: (e: string, d: unknown) => void) => {
        onEvent("error", { detail: "boom" });
      },
    );

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    const ejectBtn = await screen.findByTestId("eject-code-btn");
    fireEvent.click(ejectBtn);

    // The error is surfaced via the chat error banner (same path as sendError).
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
  });

  it("reuses a single builder session across multiple ejects", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });
    vi.mocked(api.builderSessions.create).mockResolvedValue(
      apiResp({
        id: "sess-reuse",
        team: "engineering",
        engine: "claude",
        agent_yaml: VALID_YAML,
        files: {},
        deploy_job_id: null,
        history: [],
      } as never),
    );
    vi.mocked(api.builderSessions.eject).mockImplementation(
      async (_id: string, _instruction: string, onEvent: (e: string, d: unknown) => void) => {
        onEvent("file_change", { path: "agent.py", diff: "+x", content: "print('hi')\n" });
        onEvent("complete", { summary: "done" });
      },
    );

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    // First eject (button lives on the Spec tab).
    const ejectBtn = await screen.findByTestId("eject-code-btn");
    fireEvent.click(ejectBtn);
    await waitFor(() => expect(api.builderSessions.create).toHaveBeenCalledTimes(1));
    // Eject auto-switches to the Code tab; go back to Spec to eject again.
    await waitFor(() => expect(screen.getByText("agent.py")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("artifact-tab-spec"));

    // Second eject reuses the session — create is NOT called again.
    fireEvent.click(screen.getByTestId("eject-code-btn"));
    await waitFor(() => expect(api.builderSessions.eject).toHaveBeenCalledTimes(2));
    expect(api.builderSessions.create).toHaveBeenCalledTimes(1);
  });

  it("Deploy tab shows deploy controls but not the spec YAML", async () => {
    vi.mocked(api.builders.chatStream).mockImplementation(async (_m, onEvent) => {
      onEvent("done", { assistant_message: "", agent_yaml: VALID_YAML, valid: true, errors: [] });
    });

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    // Spec tab (default) shows the YAML preview + Create/Eject controls.
    await waitFor(() => expect(screen.getByTestId("create-agent-btn")).toBeInTheDocument());
    expect(screen.getByText(/owner: alice@example.com/)).toBeInTheDocument();
    expect(screen.getByTestId("eject-code-btn")).toBeInTheDocument();

    // Switch to the Deploy tab.
    fireEvent.click(screen.getByTestId("artifact-tab-deploy"));

    // Deploy controls remain; spec YAML and Create/Eject are hidden.
    expect(screen.getByTestId("deploy-agent-btn")).toBeInTheDocument();
    expect(screen.queryByText(/owner: alice@example.com/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("create-agent-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("eject-code-btn")).not.toBeInTheDocument();
  });
});
