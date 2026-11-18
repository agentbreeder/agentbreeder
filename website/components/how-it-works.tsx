import type { ReactNode } from 'react';

interface Step {
  num: string;
  title: string;
  desc: ReactNode;
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="ab-code">{children}</code>
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
        <Code>status</Code> and <Code>reconcile</Code> re-check the principal and flag drift.{' '}
        <Code>disable</Code> blocks the principal and removes its containers.
      </>
    ),
  },
];

export function HowItWorks() {
  return (
    <section className="ab-section" aria-labelledby="how-it-works">
      <div className="ab-container">
        <p className="ab-label mb-3">how it works</p>
        <h2 id="how-it-works" className="ab-h2 mb-3">
          From a container to a gated local deploy
        </h2>
        <p className="ab-lede mb-12 max-w-[520px]">
          You bring the agent image and the identity. AgentBreeder decides whether it may run,
          and records why.
        </p>
        <ol className="m-0 grid list-none grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border p-0 md:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(({ num, title, desc }) => (
            <li key={num} className="bg-background p-6 lg:p-7">
              <p className="ab-label mb-4">{num}</p>
              <h3 className="mb-2 text-base font-semibold text-foreground">{title}</h3>
              <p className="text-sm leading-[1.65] text-muted-foreground">{desc}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
