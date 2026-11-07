'use client';

const LAYERS = [
  {
    num: '05',
    label: 'Governance & Control Plane',
    sublabel: 'The top layer — who can do what, how much it costs, what happened',
    color: '#f472b6',
    bg: 'rgba(244,114,182,0.07)',
    border: 'rgba(244,114,182,0.25)',
    items: [
      { name: 'Agent Registry', tag: 'catalog' },
      { name: 'RBAC', tag: 'access' },
      { name: 'Agentic Apps Registry', tag: 'apps' },
      { name: 'Cost Attribution', tag: 'finance' },
      { name: 'Audit Trail', tag: 'compliance' },
      { name: 'Observability', tag: 'ops' },
      { name: 'Prompt Registry', tag: 'catalog' },
      { name: 'MCP Server Hub', tag: 'tools' },
    ],
  },
  {
    num: '04',
    label: 'Agent Builder Modes',
    sublabel: 'How teams build — from drag-and-drop to full SDK control',
    color: '#22c55e',
    bg: 'rgba(34,197,94,0.07)',
    border: 'rgba(34,197,94,0.25)',
    items: [
      { name: 'No Code UI', tag: 'visual' },
      { name: 'Low Code YAML', tag: 'config' },
      { name: 'Full Code SDK', tag: 'pro' },
      { name: 'Prompt-Only', tag: 'simple' },
      { name: 'Visual Canvas', tag: 'visual' },
      { name: 'Tier Mobility', tag: 'eject' },
    ],
  },
  {
    num: '03',
    label: 'Programming Languages',
    sublabel: 'Write your agent in the language your team already uses',
    color: '#fb923c',
    bg: 'rgba(251,146,60,0.07)',
    border: 'rgba(251,146,60,0.25)',
    items: [
      { name: 'Python', tag: 'ML default' },
      { name: 'TypeScript', tag: 'web native' },
      { name: 'Kotlin', tag: 'JVM / mobile' },
      { name: 'Go', tag: 'systems' },
      { name: 'Java', tag: 'enterprise' },
      { name: 'C# / .NET', tag: 'Microsoft' },
      { name: 'Rust', tag: 'perf' },
    ],
  },
  {
    num: '02',
    label: 'Agent Frameworks & SDKs',
    sublabel: 'The frameworks that define how agents think, act, and collaborate',
    color: '#a78bfa',
    bg: 'rgba(167,139,250,0.07)',
    border: 'rgba(167,139,250,0.25)',
    items: [
      { name: 'LangGraph', tag: 'Python' },
      { name: 'CrewAI', tag: 'Python' },
      { name: 'OpenAI Agents SDK', tag: 'Py · TS' },
      { name: 'Claude Agent SDK', tag: 'Py · TS' },
      { name: 'Google ADK', tag: 'Py · TS · Go · Java' },
      { name: 'Mastra', tag: 'TypeScript' },
      { name: 'Koog', tag: 'Kotlin' },
      { name: 'PydanticAI', tag: 'Python' },
      { name: 'Agno', tag: 'Python' },
      { name: 'AutoGen / AG2', tag: 'Python' },
      { name: 'Semantic Kernel', tag: 'Py · C#' },
      { name: 'smolagents', tag: 'Python' },
      { name: 'LlamaIndex', tag: 'Py · TS' },
      { name: 'Strands', tag: 'Python' },
    ],
  },
  {
    num: '01',
    label: 'Agent Runtime & Infrastructure',
    sublabel: 'Where agents actually run — managed clouds, serverless, Kubernetes, or local',
    color: '#38bdf8',
    bg: 'rgba(56,189,248,0.07)',
    border: 'rgba(56,189,248,0.25)',
    items: [
      { name: 'AWS ECS Fargate', tag: 'AWS' },
      { name: 'AWS EKS', tag: 'AWS' },
      { name: 'Bedrock AgentCore', tag: 'AWS · GA Oct 2025' },
      { name: 'GCP Cloud Run', tag: 'GCP' },
      { name: 'GCP GKE', tag: 'GCP' },
      { name: 'Vertex Agent Engine', tag: 'GCP · GA Nov 2025' },
      { name: 'Azure Container Apps', tag: 'Azure' },
      { name: 'Claude Managed Agents', tag: 'Anthropic · GA Apr 2026' },
      { name: 'OpenAI Hosted Agents', tag: 'OpenAI' },
      { name: 'Mastra Cloud', tag: 'Mastra · Beta' },
      { name: 'Agent Bricks', tag: 'Databricks' },
      { name: 'Kubernetes', tag: 'self-hosted' },
      { name: 'Local Docker', tag: 'dev' },
    ],
  },
] as const;

const TAG_COLORS: Record<string, string> = {
  AWS: '#ff9900',
  GCP: '#4285f4',
  Azure: '#0078d4',
  Python: '#3776ab',
  TypeScript: '#3178c6',
  Kotlin: '#7f52ff',
  'Py · TS': '#a78bfa',
  'Py · C#': '#a78bfa',
  'Py · TS · Go · Java': '#a78bfa',
  'JVM / mobile': '#7f52ff',
  enterprise: '#6b7280',
  Microsoft: '#0078d4',
  systems: '#6b7280',
  perf: '#6b7280',
  'ML default': '#3776ab',
  'web native': '#3178c6',
};

function getTagColor(tag: string): string {
  return TAG_COLORS[tag] ?? '#4b5563';
}

export function AgentPlatformDiagram() {
  return (
    <div
      className="rounded-2xl overflow-hidden border"
      style={{
        borderColor: 'rgba(255,255,255,0.08)',
        background: '#0a0a0b',
        boxShadow: '0 0 80px rgba(0,0,0,0.6), 0 0 1px rgba(255,255,255,0.06) inset',
        fontFamily: 'system-ui, -apple-system, sans-serif',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-6 py-4 border-b"
        style={{ borderColor: 'rgba(255,255,255,0.06)', background: 'rgba(255,255,255,0.02)' }}
      >
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[2px] mb-0.5" style={{ color: '#4b5563' }}>
            Enterprise AI Agent Platform
          </p>
          <h3 className="text-[15px] font-bold text-white">5-Layer Architecture Stack</h3>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] rounded-full border px-2.5 py-1 font-semibold"
            style={{ borderColor: 'rgba(34,197,94,0.3)', color: '#22c55e', background: 'rgba(34,197,94,0.08)' }}
          >
            April 2026
          </span>
        </div>
      </div>

      {/* Left arrow + layers */}
      <div className="flex">
        {/* Vertical "BUILD DIRECTION" label */}
        <div
          className="flex-shrink-0 w-10 flex flex-col items-center justify-center py-6 gap-2"
          style={{ borderRight: '1px solid rgba(255,255,255,0.04)' }}
        >
          <svg width="16" height="80" viewBox="0 0 16 80" fill="none" aria-hidden>
            <line x1="8" y1="76" x2="8" y2="8" stroke="rgba(255,255,255,0.12)" strokeWidth="1" strokeDasharray="3 3" />
            <path d="M4 12L8 4L12 12" stroke="rgba(255,255,255,0.2)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <p
            className="text-[9px] font-bold uppercase tracking-[3px]"
            style={{
              color: 'rgba(255,255,255,0.15)',
              writingMode: 'vertical-rl',
              transform: 'rotate(180deg)',
              letterSpacing: '3px',
            }}
          >
            Stack
          </p>
        </div>

        {/* Layers — rendered top (05) to bottom (01) */}
        <div className="flex-1 divide-y" style={{ borderColor: 'rgba(255,255,255,0.04)' }}>
          {LAYERS.map((layer) => (
            <div
              key={layer.num}
              className="relative px-5 py-4"
              style={{ background: layer.bg }}
            >
              {/* Left accent bar */}
              <div
                className="absolute left-0 top-0 bottom-0 w-[3px]"
                style={{ background: layer.color }}
              />

              {/* Layer header row */}
              <div className="flex items-start gap-3 mb-3">
                <span
                  className="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center text-[10px] font-black"
                  style={{ background: layer.color + '22', color: layer.color, border: `1px solid ${layer.color}40` }}
                >
                  {layer.num}
                </span>
                <div className="min-w-0">
                  <h4 className="text-[13px] font-bold text-white leading-tight">{layer.label}</h4>
                  <p className="text-[11px] mt-0.5 leading-snug" style={{ color: 'rgba(255,255,255,0.35)' }}>
                    {layer.sublabel}
                  </p>
                </div>
              </div>

              {/* Items */}
              <div className="flex flex-wrap gap-1.5 pl-10">
                {layer.items.map((item) => (
                  <span
                    key={item.name}
                    className="inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5"
                    style={{
                      background: layer.color + '0d',
                      borderColor: layer.color + '30',
                    }}
                  >
                    <span className="text-[11px] font-semibold" style={{ color: 'rgba(255,255,255,0.85)' }}>
                      {item.name}
                    </span>
                    <span
                      className="text-[9px] rounded px-1 py-px font-medium"
                      style={{
                        background: getTagColor(item.tag) + '25',
                        color: getTagColor(item.tag),
                      }}
                    >
                      {item.tag}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Right: AgentBreeder vertical span */}
        <div
          className="flex-shrink-0 w-14 flex flex-col items-center justify-center gap-1 py-6"
          style={{
            borderLeft: '1px solid rgba(34,197,94,0.15)',
            background: 'rgba(34,197,94,0.03)',
          }}
        >
          <div
            className="h-full w-[2px] rounded-full"
            style={{
              background: 'linear-gradient(180deg, rgba(34,197,94,0.1) 0%, #22c55e 30%, #22c55e 70%, rgba(34,197,94,0.1) 100%)',
              minHeight: 80,
            }}
          />
          <p
            className="text-[9px] font-bold uppercase tracking-[2.5px]"
            style={{
              color: '#22c55e',
              writingMode: 'vertical-rl',
              transform: 'rotate(180deg)',
              letterSpacing: '2.5px',
            }}
          >
            AgentBreeder
          </p>
          <div
            className="h-full w-[2px] rounded-full"
            style={{
              background: 'linear-gradient(180deg, rgba(34,197,94,0.1) 0%, #22c55e 30%, #22c55e 70%, rgba(34,197,94,0.1) 100%)',
              minHeight: 80,
            }}
          />
        </div>
      </div>

      {/* Footer legend */}
      <div
        className="px-6 py-3 border-t flex flex-wrap items-center gap-x-5 gap-y-1.5"
        style={{ borderColor: 'rgba(255,255,255,0.06)', background: 'rgba(255,255,255,0.015)' }}
      >
        <p className="text-[10px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'rgba(255,255,255,0.2)' }}>Clouds</p>
        {[
          { label: 'AWS', color: '#ff9900' },
          { label: 'GCP', color: '#4285f4' },
          { label: 'Azure', color: '#0078d4' },
          { label: 'Anthropic', color: '#a78bfa' },
          { label: 'OpenAI', color: '#10a37f' },
          { label: 'Databricks', color: '#ff3621' },
          { label: 'Self-hosted', color: '#6b7280' },
        ].map(({ label, color }) => (
          <span key={label} className="flex items-center gap-1.5 text-[10px]" style={{ color: 'rgba(255,255,255,0.4)' }}>
            <span className="h-1.5 w-1.5 rounded-full flex-shrink-0" style={{ background: color }} />
            {label}
          </span>
        ))}
        <span className="ml-auto text-[10px]" style={{ color: 'rgba(34,197,94,0.5)' }}>
          agentbreeder.io
        </span>
      </div>
    </div>
  );
}
