import type { ReactNode } from 'react';

interface Step {
  num: string;
  title: string;
  desc: ReactNode;
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded px-1 py-0.5 font-mono text-xs" style={{ background: 'var(--bg-surface)' }}>
      {children}
    </code>
  );
}

const STEPS: Step[] = [
  {
    num: '01',
    title: 'Onboard the agent in your IdP',
    desc: (
      <>
        Register the agent as a workload principal in Entra, Keycloak, Okta (experimental), AWS or
        a static file, with a sponsor and a review date. AgentBreeder only reads it.
      </>
    ),
  },
  {
    num: '02',
    title: 'Describe the image',
    desc: (
      <>
        Write <Code>agent.yaml</Code>: an immutable image digest, the HTTP port and health path,
        and the identity reference. <Code>agentbreeder validate</Code> checks it without side effects.
      </>
    ),
  },
  {
    num: '03',
    title: 'Deploy through the gate',
    desc: (
      <>
        <Code>agentbreeder deploy --local</Code> resolves the principal, runs the checks you
        configured, and only then starts a locked-down container on loopback. Every attempt,
        allowed or denied, goes to the hash-chained audit log.
      </>
    ),
  },
  {
    num: '04',
    title: 'Re-verify and stop',
    desc: (
      <>
        <Code>status</Code> and <Code>reconcile</Code> re-check the principal and flag drift.
        <Code>disable</Code> blocks the principal and removes its containers.
      </>
    ),
  },
];

export function HowItWorks() {
  return (
    <section
      className="w-full border-t py-20 lg:py-28"
      style={{ borderColor: 'var(--border)' }}
    >
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          How it works
        </p>
        <h2
          className="mb-3 text-[28px] sm:text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          From a container to a gated local deploy
        </h2>
        <p className="mb-12 max-w-[500px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          You bring the agent image and the identity. AgentBreeder decides whether it may run,
          and records why.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
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
                  className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 text-lg"
                  style={{ color: 'var(--text-dim)' }}
                >
                  →
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
