import Link from 'next/link';
import { CheckIcon, CircleIcon } from './icons';

// Every "works" row was exercised end to end against main (macOS, Docker Desktop) on
// 2026-11-07, unless the row says otherwise. Rows titled "On main, coming in 3.0.0a4" are not in
// a published package; the release lane relabels them after the public-registry run. Keep this list in step with
// content/docs/index.mdx and ARCHITECTURE.md: claim nothing here that a test run hasn't shown.
const WORKS: { title: string; detail: string }[] = [
  {
    title: 'Preview: install from PyPI and npm',
    detail:
      '3.0.0a3 with pip, uvx or npx, pinned to the exact version. Run from the public registries on macOS on 2026-11-16: quickstart on all three, and Northwind 61/61 through the CLI from pip. On Linux (Ubuntu 24.04), only install and --version on all three, and doctor on pip.',
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
    title: 'Preview: local command center',
    detail:
      'agentbreeder up serves a loopback, single-user UI that runs the same CLI commands, so the gate and audit still apply. From the published 3.0.0a2 via npx on macOS on 2026-11-11, Northwind passed 61/61, driven through its API; on 3.0.0a3 it was only started and signed in through its API, with no browser.',
  },
  {
    title: 'Preview: Kubernetes on a local cluster',
    detail:
      'promote deploys an audited release by digest to a local kind cluster. Northwind passed 61/61 there from a checkout on 2026-11-10, under the restricted Pod Security level, with network policy measured, and 61/61 again from 3.0.0a2 on 2026-11-11. No production cluster has been tested.',
  },
  {
    title: 'On main, coming in 3.0.0a4: more frameworks',
    detail:
      'Runtimes for LangGraph, the OpenAI Agents SDK, Deep Agents and CrewAI, with default-deny egress. Northwind passed 61/61 on each, on local Docker and kind, on macOS (2026-11-19 and 20). Only the LangGraph starter (and one tool, with Gemini) is verified; a conformance check runs 26 of 39 checks, so no runtime is called conformant. Not in a published package yet.',
  },
  {
    title: 'On main, coming in 3.0.0a4: sub-agents and skills',
    detail:
      'runtime/adk@1.5.0 runs sub-agents and instruction-pack skills, with per-sub-agent cost and per-tag eval rates. Run end to end once each on macOS with Gemini (2026-11-19). Not in a published package yet.',
  },
  {
    title: 'On main, coming in 3.0.0a4: console sign-in',
    detail:
      'Password sign-in, Settings, re-auth and notifications in the local console, clicked through in Chrome via Playwright on macOS (2026-11-20). Still one user on one machine. Not in a published package yet.',
  },
  {
    title: 'MCP egress sidecar (opt-in)',
    detail:
      'Policy on every MCP Streamable HTTP tool call, with credential injection and held calls. STDIO and in-process tools are never mediated.',
  },
];

const NOT_YET: string[] = [
  'Cloud or production deploy targets (AWS, GCP, Azure, a real Kubernetes cluster). On main, the token cluster provider, admission policies and a services Helm chart exist but have not run on a real cluster',
  'A multi-user control center or a versioned management API',
  'Authenticated invocation of deployed agents',
  'A final 3.0.0: 3.0.0a3 is an alpha, with no stability promise',
  'Verified Linux runs: on Ubuntu 24.04 with 3.0.0a3, install and --version passed on all three paths and doctor on pip, but quickstart and Northwind have only run on macOS with Docker Desktop (Windows is unsupported)',
];

export function WhatShips() {
  return (
    <section className="ab-section" aria-labelledby="what-works">
      <div className="ab-container">
        <p className="ab-label mb-3">what works today</p>
        <h2 id="what-works" className="ab-h2 mb-3">
          Only what we have run end to end
        </h2>
        <p className="ab-lede mb-12 max-w-[620px]">
          AgentBreeder is a focused rebuild, available as the{' '}
          <Link href="/docs/install" className="ab-link">
            3.0.0a3 alpha preview
          </Link>
          . This is the complete list of what it does, and what it doesn&apos;t do yet.
        </p>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:col-span-2">
            {WORKS.map(({ title, detail }) => (
              <li key={title} className="ab-card p-5">
                <div className="mb-2 flex items-start gap-2">
                  <span className="mt-0.5 text-foreground">
                    <CheckIcon size={16} />
                  </span>
                  <h3 className="text-[15px] font-semibold leading-snug text-foreground">{title}</h3>
                </div>
                <p className="pl-6 text-sm leading-[1.65] text-muted-foreground">{detail}</p>
              </li>
            ))}
          </ul>

          <div className="ab-card h-fit bg-secondary p-6 shadow-none">
            <h3 className="mb-4 text-[15px] font-semibold text-foreground">Not shipped yet</h3>
            <ul className="m-0 list-none space-y-3 p-0">
              {NOT_YET.map((item) => (
                <li key={item} className="flex gap-2 text-sm leading-[1.6] text-muted-foreground">
                  <span className="mt-0.5 flex-shrink-0">
                    <CircleIcon size={14} />
                  </span>
                  {item}
                </li>
              ))}
            </ul>
            <p className="mt-6 border-t border-border pt-4 text-[13px] leading-[1.6] text-muted-foreground">
              Looking for the older 2.x platform on PyPI? It is unmaintained and has known issues; see{' '}
              <Link href="/docs/legacy-2x" className="ab-link">
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
