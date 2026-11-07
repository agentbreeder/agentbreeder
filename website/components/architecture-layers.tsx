const LAYERS = [
  {
    number: 1,
    label: 'Governed Marketplace',
    accent: '#22c55e',
    accentDim: 'rgba(34,197,94,0.10)',
    accentBorder: 'rgba(34,197,94,0.22)',
    description: 'Central registry — every artifact versioned, governed, and discoverable',
    items: ['Agents', 'Prompts', 'Tools', 'RAG Indexes', 'MCP Servers', 'Models'],
  },
  {
    number: 2,
    label: 'Builder Mode',
    accent: '#a78bfa',
    accentDim: 'rgba(167,139,250,0.10)',
    accentBorder: 'rgba(167,139,250,0.22)',
    description: 'Three paths, one pipeline — all compile to the same agent.yaml',
    items: ['No-Code (Visual)', 'Low-Code (YAML)', 'Full-Code (SDK)'],
  },
  {
    number: 3,
    label: 'Language',
    accent: '#60a5fa',
    accentDim: 'rgba(96,165,250,0.10)',
    accentBorder: 'rgba(96,165,250,0.22)',
    description: 'Build in the language your team already uses',
    items: ['Python', 'TypeScript'],
  },
  {
    number: 4,
    label: 'Framework SDK',
    accent: '#fb923c',
    accentDim: 'rgba(251,146,60,0.10)',
    accentBorder: 'rgba(251,146,60,0.22)',
    description: 'Every major agent framework — first-class, no lock-in',
    items: ['LangGraph', 'CrewAI', 'Claude SDK', 'OpenAI Agents', 'Google ADK', 'Custom'],
  },
  {
    number: 5,
    label: 'Deployment Runtime',
    accent: '#22d3ee',
    accentDim: 'rgba(34,211,238,0.10)',
    accentBorder: 'rgba(34,211,238,0.22)',
    description: 'Any cloud, any runtime — same agent.yaml, zero rewrites',
    items: ['AWS (ECS Fargate / EKS)', 'GCP (Cloud Run / GKE)', 'Azure (Container Apps)', 'Kubernetes', 'Local (Docker)'],
  },
];

export function ArchitectureLayers() {
  return (
    <div
      className="my-8 overflow-hidden rounded-2xl border"
      style={{ borderColor: 'rgba(255,255,255,0.10)', background: '#0d0d10' }}
    >
      {/* Header */}
      <div
        className="border-b px-6 py-4"
        style={{ borderColor: 'rgba(255,255,255,0.07)' }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[1.2px]" style={{ color: '#22c55e' }}>
          AgentBreeder Architecture
        </p>
        <p className="mt-0.5 text-[13px]" style={{ color: '#71717a' }}>
          Five layers that make agent creation framework-, cloud-, and team-agnostic
        </p>
      </div>

      {/* Layers */}
      <div>
        {LAYERS.map((layer, i) => (
          <div
            key={layer.number}
            className="border-b px-6 py-5 last:border-b-0"
            style={{ borderColor: 'rgba(255,255,255,0.06)' }}
          >
            <div className="flex items-start gap-4">
              {/* Number badge */}
              <div
                className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
                style={{
                  background: layer.accentDim,
                  border: `1px solid ${layer.accentBorder}`,
                  color: layer.accent,
                }}
              >
                {layer.number}
              </div>

              <div className="min-w-0 flex-1">
                {/* Layer name + description */}
                <div className="mb-3 flex flex-wrap items-baseline gap-2">
                  <span className="text-[15px] font-semibold text-white">
                    {layer.label}
                  </span>
                  <span className="text-[12px]" style={{ color: '#52525b' }}>
                    — {layer.description}
                  </span>
                </div>

                {/* Pills */}
                <div className="flex flex-wrap gap-2">
                  {layer.items.map((item) => (
                    <span
                      key={item}
                      className="rounded-full px-3 py-1 text-[12px] font-medium"
                      style={{
                        background: layer.accentDim,
                        border: `1px solid ${layer.accentBorder}`,
                        color: layer.accent,
                      }}
                    >
                      {item}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* Connector arrow (between layers) */}
            {i < LAYERS.length - 1 && (
              <div className="mt-4 flex justify-center">
                <svg width="16" height="12" viewBox="0 0 16 12" fill="none">
                  <path d="M8 0v8M4 6l4 4 4-4" stroke="#3f3f46" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div
        className="border-t px-6 py-3"
        style={{ borderColor: 'rgba(255,255,255,0.07)', background: 'rgba(34,197,94,0.04)' }}
      >
        <p className="text-center text-[12px] font-medium" style={{ color: '#22c55e' }}>
          Define Once · Deploy Anywhere · Govern Automatically
        </p>
      </div>
    </div>
  );
}
