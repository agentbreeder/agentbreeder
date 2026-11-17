const LAYERS = [
  {
    num: '05',
    label: 'Governance & Control Plane',
    sublabel: 'The top layer — who can do what, how much it costs, what happened',
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

// Monochrome line art with one --brand stroke, the AgentBreeder span (design v2 §7, §8.3).
// Written for a historical (2.x) post; the content is unchanged.
export function AgentPlatformDiagram() {
  return (
    <figure className="ab-diagram not-prose my-8 overflow-hidden rounded-xl border border-border bg-card">
      <figcaption className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <p className="ab-label mb-0.5">enterprise ai agent platform</p>
          <p className="text-[15px] font-semibold text-foreground">5-Layer Architecture Stack</p>
        </div>
        <span className="ab-badge">April 2026</span>
      </figcaption>

      <div className="flex">
        <div className="flex w-10 flex-shrink-0 flex-col items-center justify-center gap-2 border-r border-border py-6">
          <svg width="16" height="80" viewBox="0 0 16 80" fill="none" aria-hidden="true" className="text-control-border">
            <line x1="8" y1="76" x2="8" y2="8" stroke="currentColor" strokeWidth="1" strokeDasharray="3 3" />
            <path d="M4 12L8 4L12 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <p className="rotate-180 font-mono text-xs text-muted-foreground [writing-mode:vertical-rl]">stack</p>
        </div>

        <ol className="m-0 min-w-0 flex-1 list-none divide-y divide-border p-0">
          {LAYERS.map((layer) => (
            <li key={layer.num} className="px-4 py-4 sm:px-5">
              <div className="mb-3 flex items-start gap-3">
                <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md border border-control-border font-mono text-xs text-foreground">
                  {layer.num}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold leading-tight text-foreground">{layer.label}</p>
                  <p className="mt-0.5 text-xs leading-snug text-muted-foreground">{layer.sublabel}</p>
                </div>
              </div>
              <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0 sm:pl-10">
                {layer.items.map((item) => (
                  <li
                    key={item.name}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-0.5"
                  >
                    <span className="text-xs font-medium text-foreground">{item.name}</span>
                    <span className="font-mono text-xs text-muted-foreground">{item.tag}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>

        {/* AgentBreeder spans every layer: the gate line. */}
        <div className="flex w-12 flex-shrink-0 flex-col items-center justify-center gap-2 border-l border-border py-6">
          <span className="min-h-[80px] w-[1.5px] flex-1 bg-brand forced-colors:bg-[CanvasText]" aria-hidden="true" />
          <p className="rotate-180 font-mono text-xs text-foreground [writing-mode:vertical-rl]">agentbreeder</p>
          <span className="min-h-[80px] w-[1.5px] flex-1 bg-brand forced-colors:bg-[CanvasText]" aria-hidden="true" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border px-5 py-3">
        <p className="ab-label">clouds</p>
        {['AWS', 'GCP', 'Azure', 'Anthropic', 'OpenAI', 'Databricks', 'Self-hosted'].map((label) => (
          <span key={label} className="text-xs text-muted-foreground">
            {label}
          </span>
        ))}
        <span className="ml-auto font-mono text-xs text-muted-foreground">agentbreeder.io</span>
      </div>
    </figure>
  );
}
