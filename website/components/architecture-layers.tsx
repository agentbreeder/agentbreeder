const LAYERS = [
  {
    number: 1,
    label: 'Governed Marketplace',
    description: 'Central registry — every artifact versioned, governed, and discoverable',
    items: ['Agents', 'Prompts', 'Tools', 'RAG Indexes', 'MCP Servers', 'Models'],
  },
  {
    number: 2,
    label: 'Builder Mode',
    description: 'Three paths, one pipeline — all compile to the same agent.yaml',
    items: ['No-Code (Visual)', 'Low-Code (YAML)', 'Full-Code (SDK)'],
  },
  {
    number: 3,
    label: 'Language',
    description: 'Build in the language your team already uses',
    items: ['Python', 'TypeScript'],
  },
  {
    number: 4,
    label: 'Framework SDK',
    description: 'Every major agent framework — first-class, no lock-in',
    items: ['LangGraph', 'CrewAI', 'Claude SDK', 'OpenAI Agents', 'Google ADK', 'Custom'],
  },
  {
    number: 5,
    label: 'Deployment Runtime',
    description: 'Any cloud, any runtime — same agent.yaml, zero rewrites',
    items: ['AWS (ECS Fargate / EKS)', 'GCP (Cloud Run / GKE)', 'Azure (Container Apps)', 'Kubernetes', 'Local (Docker)'],
  },
];

// Monochrome line art with one --brand stroke (design v2 §7, §8.3). Written for a historical
// (2.x) post; the content is unchanged.
export function ArchitectureLayers() {
  return (
    <figure className="ab-diagram not-prose my-8 overflow-hidden rounded-xl border border-border bg-card">
      <figcaption className="border-b border-border px-5 py-4">
        <p className="ab-label">agentbreeder architecture</p>
        <p className="mt-0.5 text-[13px] text-muted-foreground">
          Five layers that make agent creation framework-, cloud-, and team-agnostic
        </p>
      </figcaption>

      <ol className="m-0 list-none p-0">
        {LAYERS.map((layer, i) => (
          <li key={layer.number} className="border-b border-border px-5 py-5 last:border-b-0">
            <div className="flex items-start gap-4">
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-control-border font-mono text-xs text-foreground">
                {layer.number}
              </span>
              <div className="min-w-0 flex-1">
                <div className="mb-3 flex flex-wrap items-baseline gap-2">
                  <span className="text-[15px] font-semibold text-foreground">{layer.label}</span>
                  <span className="text-[13px] text-muted-foreground">— {layer.description}</span>
                </div>
                <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
                  {layer.items.map((item) => (
                    <li key={item} className="ab-badge text-foreground">
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            {i < LAYERS.length - 1 && (
              <div className="mt-4 flex justify-center text-control-border" aria-hidden="true">
                <svg width="16" height="12" viewBox="0 0 16 12" fill="none">
                  <path d="M8 0v8M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            )}
          </li>
        ))}
      </ol>

      <div className="relative border-t border-border px-5 py-3">
        <span className="absolute inset-x-0 top-0 h-[1.5px] bg-brand forced-colors:bg-[CanvasText]" aria-hidden="true" />
        <p className="text-center font-mono text-[13px] text-foreground">Define Once · Deploy Anywhere · Govern Automatically</p>
      </div>
    </figure>
  );
}
