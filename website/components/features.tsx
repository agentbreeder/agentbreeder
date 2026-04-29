interface Feature {
  icon: string;
  title: string;
  desc: string;
}

const FEATURES: Feature[] = [
  {
    icon: '🔌',
    title: 'Framework Agnostic',
    desc: 'LangGraph, CrewAI, Claude SDK, Google ADK, OpenAI Agents. One pipeline for all frameworks — no lock-in.',
  },
  {
    icon: '☁️',
    title: 'Multi-Cloud',
    desc: 'AWS ECS · GCP Cloud Run · Azure Container Apps · Kubernetes · local. One `agent.yaml`, any target — no cloud lock-in.',
  },
  {
    icon: '🔒',
    title: 'Auto Governance',
    desc: 'RBAC, cost attribution, audit trail, and org registry registration happen automatically on every deploy.',
  },
  {
    icon: '🗂️',
    title: 'Shared Registry',
    desc: 'Agents, prompts, tools, MCP servers, models — one org-wide catalog. Search and reuse across teams.',
  },
  {
    icon: '🎯',
    title: 'Three Builder Tiers',
    desc: 'No Code → Low Code → Full Code. Start visual, eject to YAML, eject to SDK. No lock-in at any level.',
  },
  {
    icon: '🔗',
    title: 'Multi-Agent Orchestration',
    desc: '6 orchestration strategies — router, sequential, parallel, supervisor, hierarchical, fan-out — via YAML or SDK.',
  },
  {
    icon: '🏆',
    title: 'LLM-as-Judge Eval Hub',
    desc: 'Multi-criteria scoring (accuracy, helpfulness, safety, groundedness) via Claude, GPT-4o, or Gemini. Public leaderboard, regression detection, CSV export.',
  },
  {
    icon: '🛒',
    title: 'Provider Catalog (v2.0)',
    desc: '9 OpenAI-compatible presets out of the box — Nvidia NIM, Kimi K2, Groq, Together, Fireworks, DeepInfra, Cerebras, Hyperbolic, OpenRouter. `agentbreeder provider add` for any custom upstream.',
  },
  {
    icon: '🛡️',
    title: 'Sidecar Pattern (v2.0)',
    desc: 'Single Go binary auto-injected next to every agent. Bearer auth, OTel tracing, cost attribution, PII guardrails, A2A JSON-RPC, MCP passthrough — zero per-language re-implementation.',
  },
  {
    icon: '🔐',
    title: 'Workspace Secrets (v2.0)',
    desc: 'OS keychain · Vault · AWS · GCP — pick one per workspace. `agentbreeder deploy` auto-mirrors declared secrets to the target cloud and grants the runtime SA `secretAccessor`. No plaintext in the image.',
  },
  {
    icon: '🌐',
    title: 'Polyglot Runtime Contract (v2.0)',
    desc: 'Versioned HTTP contract every agent satisfies. First-party Go SDK ships v2.0 (Kotlin, Rust, .NET on the way). Any language with an HTTP server is a Tier-3 citizen — generate from the OpenAPI.',
  },
  {
    icon: '🔀',
    title: 'Gateways as First-Class (v2.0)',
    desc: 'LiteLLM and OpenRouter promoted to `type: gateway` providers. Three-segment refs route the request: `model: openrouter/moonshotai/kimi-k2`. Configure once per workspace; switch upstreams without touching code.',
  },
];

export function Features() {
  return (
    <section className="w-full py-20 lg:py-28">
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          Why AgentBreeder
        </p>
        <h2
          className="mb-3 text-[28px] sm:text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          Everything you need to ship agents
        </h2>
        <p className="mb-12 max-w-[500px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          Stop reinventing deployment, governance, and observability for every agent.
          AgentBreeder handles it automatically.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {FEATURES.map(({ icon, title, desc }) => (
            <div
              key={title}
              className="rounded-[14px] border p-6 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg"
              style={{
                background: 'var(--bg-surface)',
                borderColor: 'var(--border)',
              }}
            >
              <div className="mb-3 text-[22px]">{icon}</div>
              <h3 className="mb-1.5 text-[15px] font-bold text-white">{title}</h3>
              <p className="text-[13px] leading-[1.65]" style={{ color: 'var(--text-muted)' }}>{desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
