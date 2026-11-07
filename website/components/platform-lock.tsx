import Link from 'next/link';

const PLATFORMS = [
  {
    name: 'Google Gemini Agent Platform',
    announced: 'Apr 2026',
    deployTarget: 'GCP only',
    frameworks: 'ADK only',
    openSource: false,
    dataResidency: 'Google Cloud',
    highlight: false,
  },
  {
    name: 'Claude Managed Agents',
    announced: 'Apr 2026',
    deployTarget: 'Anthropic only',
    frameworks: 'Claude only',
    openSource: false,
    dataResidency: 'Anthropic infra',
    highlight: false,
  },
  {
    name: 'OpenAI Agent Platform',
    announced: '2025',
    deployTarget: 'OpenAI API only',
    frameworks: 'OpenAI SDK only',
    openSource: false,
    dataResidency: 'OpenAI infra',
    highlight: false,
  },
  {
    name: 'Azure AI Foundry',
    announced: 'May 2025',
    deployTarget: 'Azure only',
    frameworks: 'Foundry SDK / LangGraph',
    openSource: false,
    dataResidency: 'Azure',
    highlight: false,
  },
  {
    name: 'AgentBreeder',
    announced: 'Open source',
    deployTarget: 'AWS · GCP · Azure · K8s · local',
    frameworks: 'LangGraph · CrewAI · Claude · OpenAI · ADK · Custom',
    openSource: true,
    dataResidency: 'Your infra',
    highlight: true,
  },
];

const CHECK = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
    <circle cx="7" cy="7" r="7" fill="#22c55e" fillOpacity="0.15" />
    <path d="M4 7l2 2 4-4" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const CROSS = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
    <circle cx="7" cy="7" r="7" fill="#ef4444" fillOpacity="0.1" />
    <path d="M5 5l4 4M9 5l-4 4" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

export function PlatformLock() {
  return (
    <section style={{ background: 'var(--bg-base)' }}>
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-20 lg:py-28">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          The Market in April 2026
        </p>
        <h2
          className="mb-3 text-[28px] sm:text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          Every major platform just shipped.<br />
          Every one locks you in.
        </h2>
        <p className="mb-10 max-w-[560px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          Google, Anthropic, OpenAI, and Azure all announced enterprise agent platforms in 2025–2026.
          Each requires your agents to run on their infrastructure. AgentBreeder deploys to any cloud — and makes governance automatic regardless of where you run.
        </p>

        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto rounded-[14px] border" style={{ borderColor: 'var(--border-hover)' }}>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)' }}>
                <th className="text-left px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Platform</th>
                <th className="text-left px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Deploy target</th>
                <th className="text-left px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Frameworks</th>
                <th className="text-center px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Open source</th>
                <th className="text-left px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Your data runs on</th>
              </tr>
            </thead>
            <tbody>
              {PLATFORMS.map((p, i) => (
                <tr
                  key={p.name}
                  style={{
                    borderBottom: i < PLATFORMS.length - 1 ? '1px solid var(--border)' : 'none',
                    background: p.highlight
                      ? 'linear-gradient(90deg, rgba(34,197,94,0.06) 0%, rgba(34,197,94,0.02) 100%)'
                      : 'transparent',
                  }}
                >
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-2.5">
                      {p.highlight && (
                        <span
                          className="rounded-full px-2 py-0.5 text-[10px] font-bold"
                          style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--accent-border)' }}
                        >
                          YOU
                        </span>
                      )}
                      <span className={p.highlight ? 'font-bold text-white text-[14px]' : 'text-[13px]'} style={{ color: p.highlight ? 'white' : 'var(--text-muted)' }}>
                        {p.name}
                      </span>
                    </div>
                    <div className="mt-0.5 text-[11px]" style={{ color: 'var(--text-dim)' }}>{p.announced}</div>
                  </td>
                  <td className="px-5 py-4">
                    <span
                      className="text-[12px] font-mono"
                      style={{ color: p.highlight ? 'var(--accent)' : '#ef4444' }}
                    >
                      {p.deployTarget}
                    </span>
                  </td>
                  <td className="px-5 py-4">
                    <span className="text-[12px]" style={{ color: p.highlight ? '#86efac' : 'var(--text-muted)' }}>
                      {p.frameworks}
                    </span>
                  </td>
                  <td className="px-5 py-4 text-center">
                    <span className="inline-flex justify-center">
                      {p.openSource ? CHECK : CROSS}
                    </span>
                  </td>
                  <td className="px-5 py-4">
                    <span
                      className="text-[12px]"
                      style={{ color: p.highlight ? 'var(--accent)' : 'var(--text-dim)' }}
                    >
                      {p.dataResidency}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile cards */}
        <div className="md:hidden space-y-3">
          {PLATFORMS.map(p => (
            <div
              key={p.name}
              className="rounded-[14px] border p-4"
              style={{
                borderColor: p.highlight ? 'var(--accent-border)' : 'var(--border)',
                background: p.highlight ? 'rgba(34,197,94,0.05)' : 'var(--bg-surface)',
              }}
            >
              <div className="flex items-center gap-2 mb-3">
                {p.highlight && (
                  <span className="rounded-full px-2 py-0.5 text-[10px] font-bold" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--accent-border)' }}>YOU</span>
                )}
                <span className="font-bold text-white text-[13px]">{p.name}</span>
                <span className="ml-auto text-[10px]" style={{ color: 'var(--text-dim)' }}>{p.announced}</span>
              </div>
              <div className="space-y-1.5">
                <div className="flex justify-between text-[12px]">
                  <span style={{ color: 'var(--text-dim)' }}>Deploy target</span>
                  <span style={{ color: p.highlight ? 'var(--accent)' : '#ef4444' }} className="font-mono">{p.deployTarget}</span>
                </div>
                <div className="flex justify-between text-[12px]">
                  <span style={{ color: 'var(--text-dim)' }}>Open source</span>
                  <span>{p.openSource ? '✓' : '✗'}</span>
                </div>
                <div className="flex justify-between text-[12px]">
                  <span style={{ color: 'var(--text-dim)' }}>Your data on</span>
                  <span style={{ color: p.highlight ? 'var(--accent)' : 'var(--text-muted)' }}>{p.dataResidency}</span>
                </div>
              </div>
            </div>
          ))}
        </div>

        <p className="mt-6 text-[13px]" style={{ color: 'var(--text-dim)' }}>
          * Google Gemini Enterprise Agent Platform announced April 22, 2026.
          Claude Managed Agents GA April 2026. Azure AI Foundry GA May 2025.
          <Link href="/blog/the-big-four-agent-platforms-2026" className="ml-1 underline" style={{ color: 'var(--accent)' }}>
            Read the full analysis →
          </Link>
        </p>
      </div>
    </section>
  );
}
