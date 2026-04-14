import type { ReactNode } from 'react';

interface Step {
  num: string;
  title: string;
  desc: ReactNode;
}

const STEPS: Step[] = [
  {
    num: '01',
    title: 'Define your agent',
    desc: (
      <>
        Write{' '}
        <code
          className="rounded px-1 py-0.5 font-mono text-xs"
          style={{ background: 'var(--bg-surface)' }}
        >
          agent.yaml
        </code>{' '}
        with your model, tools, prompts, and deploy config. Schema-validated,
        human-readable, version-controlled.
      </>
    ),
  },
  {
    num: '02',
    title: 'Run one command',
    desc: (
      <>
        <code
          className="rounded px-1 py-0.5 font-mono text-xs"
          style={{ background: 'var(--bg-surface)' }}
        >
          agentbreeder deploy
        </code>{' '}
        validates, builds a container, provisions infra, and deploys to your cloud
        in under 5 minutes.
      </>
    ),
  },
  {
    num: '03',
    title: 'Governance is automatic',
    desc: 'Your agent is live, registered in the org registry, RBAC enforced, costs attributed, audit trail written. Nothing extra to configure.',
  },
];

export function HowItWorks() {
  return (
    <section
      className="mx-auto max-w-[1200px] border-t px-20 py-20"
      style={{ borderColor: 'var(--border)' }}
    >
      <p
        className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
        style={{ color: 'var(--accent)' }}
      >
        How it works
      </p>
      <h2
        className="mb-3 text-[36px] font-extrabold text-white"
        style={{ letterSpacing: '-1px' }}
      >
        From YAML to production in 3 steps
      </h2>
      <p className="mb-12 max-w-[500px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
        No infrastructure expertise required. AgentBreeder handles the entire
        deploy pipeline.
      </p>
      <div className="grid grid-cols-3 gap-6">
        {STEPS.map(({ num, title, desc }, i) => (
          <div key={num} className="relative p-7">
            <p
              className="mb-3.5 font-mono text-[11px] font-semibold tracking-[1px]"
              style={{ color: 'var(--accent)' }}
            >
              {num} ──
            </p>
            <h3 className="mb-2 text-[16px] font-bold text-white">{title}</h3>
            <p className="text-[13px] leading-[1.65]" style={{ color: 'var(--text-muted)' }}>
              {desc}
            </p>
            {i < STEPS.length - 1 && (
              <span
                className="absolute right-0 top-1/2 -translate-y-1/2 text-lg"
                style={{ color: 'var(--text-dim)' }}
              >
                →
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
