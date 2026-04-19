const FRAMEWORKS = ['LangGraph', 'CrewAI', 'Claude SDK', 'Google ADK', 'OpenAI Agents', 'Custom'];

export function Frameworks() {
  return (
    <div
      className="flex flex-col sm:flex-row flex-wrap items-start sm:items-center gap-4 sm:gap-8 border-y px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-7"
      style={{ borderColor: 'var(--border)' }}
    >
      <span
        className="flex-shrink-0 text-xs tracking-wide"
        style={{ color: 'var(--text-dim)' }}
      >
        Works with every major framework
      </span>
      <div className="flex flex-wrap gap-2.5">
        {FRAMEWORKS.map((fw) => (
          <span
            key={fw}
            className="rounded-full border px-3.5 py-1 text-xs transition-colors"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)', background: 'var(--bg-surface)' }}
          >
            {fw}
          </span>
        ))}
      </div>
    </div>
  );
}
