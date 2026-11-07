const CHECK = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
    <circle cx="7" cy="7" r="7" fill="rgba(34,197,94,0.15)" />
    <path d="M4 7l2 2 4-4" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const PARTIAL = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
    <circle cx="7" cy="7" r="7" fill="rgba(251,146,60,0.12)" />
    <path d="M4 7h6" stroke="#fb923c" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const CROSS = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
    <circle cx="7" cy="7" r="7" fill="rgba(239,68,68,0.10)" />
    <path d="M5 5l4 4M9 5l-4 4" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

type CellValue = 'yes' | 'partial' | 'no';

interface Row {
  feature: string;
  agentbreeder: CellValue;
  langgraph: CellValue;
  crewai: CellValue;
  openai: CellValue;
  googleadk: CellValue;
  claudesdk: CellValue;
  mastra: CellValue;
  bedrock: CellValue;
  note?: string;
}

const ROWS: Row[] = [
  {
    feature: 'Framework agnostic',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'no', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'no',
    note: 'Run LangGraph, CrewAI, ADK, Mastra, Claude SDK, or your own code — same pipeline',
  },
  {
    feature: 'Cloud agnostic (AWS + GCP + Azure)',
    agentbreeder: 'yes', langgraph: 'partial', crewai: 'partial', openai: 'no', googleadk: 'no', claudesdk: 'partial', mastra: 'yes', bedrock: 'no',
    note: 'Bedrock = AWS only · ADK = GCP preferred · OpenAI = OpenAI cloud · Mastra = runs anywhere',
  },
  {
    feature: 'No-code visual builder',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'partial', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'partial',
  },
  {
    feature: 'YAML low-code config',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'no', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'no',
  },
  {
    feature: 'Full-code SDK',
    agentbreeder: 'yes', langgraph: 'yes', crewai: 'yes', openai: 'yes', googleadk: 'yes', claudesdk: 'yes', mastra: 'yes', bedrock: 'yes',
  },
  {
    feature: 'Built-in RBAC & governance',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'partial', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'partial',
    note: 'Governance is a side effect of deploying, not a separate project',
  },
  {
    feature: 'Cost attribution per team',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'partial', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'partial',
  },
  {
    feature: 'Immutable audit log',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'no', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'partial',
  },
  {
    feature: 'Shared org-wide registry',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'no', googleadk: 'no', claudesdk: 'no', mastra: 'no', bedrock: 'no',
    note: 'Agents, prompts, tools, RAGs, MCPs — discoverable across all teams',
  },
  {
    feature: 'Multi-language (Python + TypeScript)',
    agentbreeder: 'yes', langgraph: 'no', crewai: 'no', openai: 'partial', googleadk: 'no', claudesdk: 'partial', mastra: 'no', bedrock: 'partial',
    note: 'Mastra = TypeScript only · Claude SDK = Python + TS · most others = Python only',
  },
  {
    feature: 'MCP server support',
    agentbreeder: 'yes', langgraph: 'partial', crewai: 'partial', openai: 'yes', googleadk: 'partial', claudesdk: 'yes', mastra: 'yes', bedrock: 'no',
  },
  {
    feature: 'Open source (Apache 2.0)',
    agentbreeder: 'yes', langgraph: 'yes', crewai: 'yes', openai: 'no', googleadk: 'yes', claudesdk: 'partial', mastra: 'yes', bedrock: 'no',
    note: 'Claude SDK is MIT-licensed; Claude models are proprietary',
  },
];

const ICON: Record<CellValue, React.ReactNode> = { yes: CHECK, partial: PARTIAL, no: CROSS };
const LABEL: Record<CellValue, string> = { yes: 'Yes', partial: 'Partial', no: 'No' };

const COLS: { key: keyof Omit<Row, 'feature' | 'note'>; label: string; highlight: boolean }[] = [
  { key: 'agentbreeder', label: 'AgentBreeder',  highlight: true },
  { key: 'langgraph',    label: 'LangGraph',     highlight: false },
  { key: 'crewai',       label: 'CrewAI',        highlight: false },
  { key: 'openai',       label: 'OpenAI Agents', highlight: false },
  { key: 'googleadk',    label: 'Google ADK',    highlight: false },
  { key: 'claudesdk',    label: 'Claude SDK',    highlight: false },
  { key: 'mastra',       label: 'Mastra',        highlight: false },
  { key: 'bedrock',      label: 'AWS Bedrock',   highlight: false },
];

function Cell({ value, highlight }: { value: CellValue; highlight: boolean }) {
  return (
    <td
      className="px-4 py-3 text-center"
      style={highlight ? { background: 'rgba(34,197,94,0.04)' } : undefined}
    >
      <span className="inline-flex items-center justify-center gap-1.5">
        {ICON[value]}
        <span className="text-[11px]" style={{ color: value === 'yes' ? '#22c55e' : value === 'partial' ? '#fb923c' : 'var(--text-dim)' }}>
          {LABEL[value]}
        </span>
      </span>
    </td>
  );
}

export function FrameworkComparison() {
  return (
    <div className="my-8 overflow-x-auto rounded-2xl border" style={{ borderColor: 'rgba(255,255,255,0.10)', background: '#0d0d10' }}>
      {/* Header */}
      <div className="border-b px-6 py-4" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
        <p className="text-[11px] font-semibold uppercase tracking-[1.2px]" style={{ color: '#22c55e' }}>
          Framework Comparison
        </p>
        <p className="mt-0.5 text-[13px]" style={{ color: '#71717a' }}>
          AgentBreeder vs leading agent frameworks — capability by capability
        </p>
      </div>

      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
            <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.6px]" style={{ color: 'var(--text-dim)', minWidth: 220 }}>
              Capability
            </th>
            {COLS.map((col) => (
              <th
                key={col.key}
                className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-[0.6px]"
                style={{
                  color: col.highlight ? '#22c55e' : '#71717a',
                  background: col.highlight ? 'rgba(34,197,94,0.06)' : undefined,
                  minWidth: 120,
                }}
              >
                {col.highlight && (
                  <span className="mb-1 block rounded-full px-2 py-0.5 text-[9px]" style={{ background: 'rgba(34,197,94,0.15)', color: '#22c55e' }}>
                    ★ This
                  </span>
                )}
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row, i) => (
            <tr
              key={row.feature}
              style={{ borderBottom: i < ROWS.length - 1 ? '1px solid rgba(255,255,255,0.05)' : undefined }}
            >
              <td className="px-4 py-3" style={{ color: '#e4e4e7' }}>
                <div>{row.feature}</div>
                {row.note && (
                  <div className="mt-0.5 text-[11px]" style={{ color: 'var(--text-dim)' }}>{row.note}</div>
                )}
              </td>
              {COLS.map((col) => (
                <Cell key={col.key} value={row[col.key]} highlight={col.highlight} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-5 border-t px-6 py-3" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
        <span className="text-[11px]" style={{ color: 'var(--text-dim)' }}>Legend:</span>
        {([['yes', '#22c55e', CHECK], ['partial', '#fb923c', PARTIAL], ['no', 'var(--text-dim)', CROSS]] as const).map(([label, color, icon]) => (
          <span key={label} className="inline-flex items-center gap-1.5 text-[11px]" style={{ color }}>
            {icon} {label === 'yes' ? 'Full support' : label === 'partial' ? 'Partial / vendor-specific' : 'Not supported'}
          </span>
        ))}
      </div>
    </div>
  );
}
