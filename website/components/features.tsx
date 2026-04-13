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
    desc: 'GCP Cloud Run and local Docker Compose today. AWS ECS planned. Same command, any cloud.',
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
];

export function Features() {
  return (
    <section className="mx-auto max-w-[1200px] px-20 py-20">
      <p
        className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
        style={{ color: 'var(--accent)' }}
      >
        Why AgentBreeder
      </p>
      <h2
        className="mb-3 text-[36px] font-extrabold text-white"
        style={{ letterSpacing: '-1px' }}
      >
        Everything you need to ship agents
      </h2>
      <p className="mb-12 max-w-[500px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
        Stop reinventing deployment, governance, and observability for every agent.
        AgentBreeder handles it automatically.
      </p>
      <div className="grid grid-cols-3 gap-4">
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
    </section>
  );
}
