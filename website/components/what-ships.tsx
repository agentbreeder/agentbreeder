import Link from 'next/link';

// Every "works" row was exercised end to end against main (macOS, Docker Desktop) on
// 2026-11-07, unless the row says otherwise. Keep this list in step with
// content/docs/index.mdx and ARCHITECTURE.md: claim nothing here that a test run hasn't shown.
const WORKS: { title: string; detail: string }[] = [
  {
    title: 'Preview: install from PyPI and npm',
    detail:
      '3.0.0a1 with pip, uvx or npx, pinned to the exact version. Run from the public registries on 2026-11-10: quickstart on all three and Northwind (61/61, pip) on macOS; install, --version and doctor only on Linux.',
  },
  {
    title: 'Manifest validation and doctor',
    detail: 'Strict, size-bounded agent.yaml checks with no side effects; a read-only Docker diagnostic.',
  },
  {
    title: 'Local Docker lifecycle',
    detail:
      'deploy, status and remove with SQLite history. Retrying the same manifest resumes the same deployment.',
  },
  {
    title: 'Locked-down containers',
    detail: 'UID 65532, read-only filesystem, all capabilities dropped, published on loopback only.',
  },
  {
    title: 'Deploy-time identity gate',
    detail:
      'Enabled principal, active sponsor, current review date, roles, groups and attributes. Verified end to end with the static-file adapter; Entra, Keycloak, Okta (experimental) and AWS are tested against recorded responses only.',
  },
  {
    title: 'Policy, approval and kill switch',
    detail:
      'Cedar policy, second-person approval bound to the exact revision, and a persistent per-principal blocker. OPA is tested against a real server in the test suite; AuthZEN in unit tests only.',
  },
  {
    title: 'Drift checks',
    detail: 'status and reconcile re-resolve the principal; reconcile exits 4 when anything is noncompliant.',
  },
  {
    title: 'Hash-chained audit log',
    detail:
      'Every attempt, allowed or denied. Detects corruption; a database owner can recompute the chain, so it is not tamper-evidence.',
  },
  {
    title: 'Preview: build from agent.yaml',
    detail:
      'Versioned, hash-locked registries for runtimes, prompts, knowledge, tools and evals; an ADK runtime; per-deployment LiteLLM keys; an eval-gated release. Verified only with the Northwind example, which passed 61/61.',
  },
  {
    title: 'Preview: local backing services from stack.yaml',
    detail:
      'agentbreeder services up starts Postgres with pgvector, a LiteLLM gateway, Phoenix and project services on local Docker, hardened and hash-locked, and writes a demo operator config. Local only; verified with the Northwind example (61/61).',
  },
  {
    title: 'MCP egress sidecar (opt-in)',
    detail:
      'Policy on every MCP Streamable HTTP tool call, with credential injection and held calls. STDIO and in-process tools are never mediated.',
  },
];

const NOT_YET: string[] = [
  'Cloud or production deploy targets (AWS, GCP, Azure, Kubernetes)',
  'A management API or web UI',
  'Authenticated invocation of deployed agents',
  'A final 3.0.0: 3.0.0a1 is an alpha, with no stability promise',
  'Verified Linux runs: install and doctor pass on Ubuntu 24.04, but quickstart and Northwind have only run on macOS with Docker Desktop (Windows is unsupported)',
];

export function WhatShips() {
  return (
    <section className="w-full border-t py-20 lg:py-28" style={{ borderColor: 'var(--border)' }}>
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]" style={{ color: 'var(--accent)' }}>
          What works today
        </p>
        <h2 className="mb-3 text-[28px] sm:text-[36px] font-extrabold text-white" style={{ letterSpacing: '-1px' }}>
          Only what we have run end to end
        </h2>
        <p className="mb-12 max-w-[620px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          AgentBreeder is a focused rebuild, available as the{' '}
          <Link href="/docs/install" style={{ color: 'var(--text)' }}>
            3.0.0a1 alpha preview
          </Link>
          . This is the complete list of what it does, and what it doesn&apos;t do yet.
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-3">
            {WORKS.map(({ title, detail }) => (
              <div
                key={title}
                className="rounded-xl border p-5"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="mb-2 flex items-center gap-2">
                  <span aria-hidden style={{ color: 'var(--accent)' }}>✓</span>
                  <h3 className="text-[14px] font-bold text-white">{title}</h3>
                </div>
                <p className="text-[13px] leading-[1.65]" style={{ color: 'var(--text-muted)' }}>
                  {detail}
                </p>
              </div>
            ))}
          </div>

          <div
            className="rounded-xl border p-6 h-fit"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
          >
            <h3 className="mb-4 text-[14px] font-bold text-white">Not shipped yet</h3>
            <ul className="space-y-3">
              {NOT_YET.map((item) => (
                <li key={item} className="flex gap-2 text-[13px] leading-[1.6]" style={{ color: 'var(--text-muted)' }}>
                  <span aria-hidden style={{ color: 'var(--text-dim)' }}>○</span>
                  {item}
                </li>
              ))}
            </ul>
            <p className="mt-6 border-t pt-4 text-[12px] leading-[1.6]" style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}>
              Looking for the older 2.x platform on PyPI? It is unmaintained and has known issues; see{' '}
              <Link href="/docs/legacy-2x" style={{ color: 'var(--text-muted)' }}>
                Legacy 2.x
              </Link>
              .
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
