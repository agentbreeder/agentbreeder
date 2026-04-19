const IS_ITEMS = [
  {
    title: 'A deployment & governance layer',
    body: 'AgentBreeder sits above every framework. It packages, deploys, and governs agents regardless of how they were built.',
  },
  {
    title: 'Framework-agnostic infrastructure',
    body: 'LangGraph, CrewAI, OpenAI Agents, Google ADK, Claude SDK — all deploy through the same pipeline, same governance, same registry.',
  },
  {
    title: 'Multi-cloud by default',
    body: 'AWS, GCP, Azure, Databricks, Kubernetes, or local Docker — one agent.yaml, any target, no rewrites.',
  },
  {
    title: 'An org-wide shared registry',
    body: 'Agents, prompts, tools, RAG indexes, and MCP servers are versioned, discoverable, and reusable across every team.',
  },
  {
    title: 'Governance as a side effect',
    body: 'RBAC, cost attribution, audit log, and observability inject automatically at deploy time — never bolt-on, never skippable.',
  },
  {
    title: 'A builder for every skill level',
    body: 'No-code (visual canvas), low-code (YAML), and full-code (SDK) — all compiling to the same internal format.',
  },
];

const IS_NOT_ITEMS = [
  {
    title: 'Not a new agent framework',
    body: 'AgentBreeder does not replace LangGraph, CrewAI, or any other framework. It deploys them. Your team keeps the tools they know.',
  },
  {
    title: 'Not a single-cloud service',
    body: 'Unlike AWS Bedrock or Vertex AI, AgentBreeder has no cloud allegiance. There is no lock-in at the infrastructure layer.',
  },
  {
    title: 'Not a model provider',
    body: 'AgentBreeder does not host or serve LLMs. It routes to whatever model your team selects — Claude, GPT-4o, Gemini, Llama, or a fine-tune.',
  },
  {
    title: 'Not a Python-only platform',
    body: 'Unlike most agent frameworks, AgentBreeder supports Python, TypeScript, and will support Java and Kotlin — because your teams are not all Python shops.',
  },
  {
    title: 'Not a research project',
    body: 'AgentBreeder is production infrastructure. It is opinionated about governance, deployment, and reliability — not just experimentation.',
  },
  {
    title: 'Not another no-code-only wrapper',
    body: 'The no-code builder produces the same agent.yaml as the full-code SDK. Nothing is hidden, simplified away, or dumbed down for engineering teams.',
  },
];

function Item({ title, body, positive }: { title: string; body: string; positive: boolean }) {
  const accent = positive ? '#22c55e' : '#ef4444';
  const accentDim = positive ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.06)';
  const accentBorder = positive ? 'rgba(34,197,94,0.18)' : 'rgba(239,68,68,0.15)';
  const icon = positive ? '✓' : '✕';

  return (
    <div
      className="flex gap-3 rounded-xl border p-4"
      style={{ background: accentDim, borderColor: accentBorder }}
    >
      <span
        className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
        style={{ background: positive ? 'rgba(34,197,94,0.18)' : 'rgba(239,68,68,0.15)', color: accent }}
      >
        {icon}
      </span>
      <div>
        <p className="mb-1 text-[13px] font-semibold text-white">{title}</p>
        <p className="text-[12px] leading-relaxed" style={{ color: '#71717a' }}>{body}</p>
      </div>
    </div>
  );
}

export function IsIsNot() {
  return (
    <div className="my-8 overflow-hidden rounded-2xl border" style={{ borderColor: 'rgba(255,255,255,0.10)', background: '#0d0d10' }}>
      {/* Header */}
      <div className="border-b px-6 py-4" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
        <p className="text-[11px] font-semibold uppercase tracking-[1.2px]" style={{ color: '#22c55e' }}>
          Positioning
        </p>
        <p className="mt-0.5 text-[13px]" style={{ color: '#71717a' }}>
          What AgentBreeder is — and what it deliberately is not
        </p>
      </div>

      <div className="grid grid-cols-1 gap-0 md:grid-cols-2">
        {/* IS */}
        <div className="border-b p-6 md:border-b-0 md:border-r" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
          <p className="mb-4 text-[12px] font-bold uppercase tracking-[1px]" style={{ color: '#22c55e' }}>
            AgentBreeder IS
          </p>
          <div className="flex flex-col gap-3">
            {IS_ITEMS.map((item) => (
              <Item key={item.title} title={item.title} body={item.body} positive={true} />
            ))}
          </div>
        </div>

        {/* IS NOT */}
        <div className="p-6">
          <p className="mb-4 text-[12px] font-bold uppercase tracking-[1px]" style={{ color: '#ef4444' }}>
            AgentBreeder IS NOT
          </p>
          <div className="flex flex-col gap-3">
            {IS_NOT_ITEMS.map((item) => (
              <Item key={item.title} title={item.title} body={item.body} positive={false} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
