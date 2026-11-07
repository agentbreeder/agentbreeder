'use client';

import { useState } from 'react';

const LAYERS = [
  {
    id: 'build',
    number: '01',
    label: 'Build Layer',
    subtitle: 'Three builder tiers — all compile to the same format',
    color: '#a78bfa',
    items: [
      { icon: '🖱️', name: 'No Code', desc: 'Visual canvas, drag-and-drop', badge: 'ReactFlow' },
      { icon: '📄', name: 'Low Code', desc: 'YAML in any IDE', badge: 'agent.yaml' },
      { icon: '🧑‍💻', name: 'Full Code', desc: 'Python / TypeScript SDK', badge: 'SDK' },
    ],
    output: 'agent.yaml + optional code',
  },
  {
    id: 'spec',
    number: '02',
    label: 'Universal Spec',
    subtitle: 'One portable config. Cloud-neutral. Human-readable.',
    color: '#22c55e',
    items: [
      { icon: '📦', name: 'Identity', desc: 'name, version, team, owner', badge: 'required' },
      { icon: '🤖', name: 'Model', desc: 'primary, fallback, gateway', badge: 'any provider' },
      { icon: '⚡', name: 'Framework', desc: 'langgraph · crewai · claude_sdk · adk · custom', badge: 'any' },
      { icon: '🚀', name: 'Deploy', desc: 'cloud, runtime, scaling, secrets', badge: 'any cloud' },
    ],
    output: 'Parsed & validated config',
  },
  {
    id: 'pipeline',
    number: '03',
    label: 'Deploy Pipeline',
    subtitle: '8-step atomic pipeline. All-or-nothing. No partial deploys.',
    color: '#fb923c',
    items: [
      { icon: '✓', name: 'Parse & Validate', desc: 'JSON Schema + semantic checks', badge: 'step 1' },
      { icon: '✓', name: 'RBAC Check', desc: 'Fail fast if unauthorized', badge: 'step 2' },
      { icon: '✓', name: 'Dependency Resolution', desc: 'Fetch all registry refs', badge: 'step 3' },
      { icon: '✓', name: 'Container Build', desc: 'Framework-specific Dockerfile', badge: 'step 4' },
      { icon: '✓', name: 'Infra Provision', desc: 'Cloud SDK calls (boto3 · gcloud · k8s)', badge: 'step 5' },
      { icon: '✓', name: 'Deploy & Health Check', desc: 'Rolling deploy + smoke test', badge: 'step 6' },
      { icon: '✓', name: 'Register in Org', desc: 'Auto-register in registry', badge: 'step 7' },
      { icon: '✓', name: 'Return Endpoint', desc: 'Live URL + deployment ID', badge: 'step 8' },
    ],
    output: 'Running container + endpoint URL',
  },
  {
    id: 'runtime',
    number: '04',
    label: 'Runtime Layer',
    subtitle: 'Same agent. Any cloud. No code changes.',
    color: '#38bdf8',
    items: [
      { icon: '🟠', name: 'AWS ECS Fargate', desc: 'Serverless containers', badge: 'aws' },
      { icon: '🟠', name: 'AWS App Runner', desc: 'Fully managed', badge: 'aws' },
      { icon: '🟠', name: 'AWS EKS', desc: 'Kubernetes on AWS', badge: 'aws' },
      { icon: '🔵', name: 'GCP Cloud Run', desc: 'Serverless containers', badge: 'gcp' },
      { icon: '🔵', name: 'GCP GKE', desc: 'Kubernetes on GCP', badge: 'gcp' },
      { icon: '🟣', name: 'Azure Container Apps', desc: 'Serverless containers', badge: 'azure' },
      { icon: '⚙️', name: 'Kubernetes', desc: 'EKS / GKE / AKS / self-hosted', badge: 'k8s' },
      { icon: '🟢', name: 'Local Docker', desc: 'Docker Compose', badge: 'local' },
    ],
    output: 'Live agent endpoint',
  },
  {
    id: 'governance',
    number: '05',
    label: 'Governance Layer',
    subtitle: 'Automatic on every deploy. No extra configuration.',
    color: '#f472b6',
    items: [
      { icon: '🔒', name: 'RBAC', desc: 'Validated before build starts', badge: 'automatic' },
      { icon: '🗂️', name: 'Org Registry', desc: 'Auto-registered after deploy', badge: 'automatic' },
      { icon: '💰', name: 'Cost Attribution', desc: 'Attributed to team at deploy time', badge: 'automatic' },
      { icon: '📋', name: 'Audit Trail', desc: 'Every deploy logged immutably', badge: 'automatic' },
    ],
    output: 'Governed agent fleet',
  },
] as const;

export function FiveLayerArch() {
  const [active, setActive] = useState<string | null>(null);

  return (
    <section style={{ background: 'var(--bg-base)' }}>
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-20 lg:py-28">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          Architecture
        </p>
        <h2
          className="mb-3 text-[28px] sm:text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          Five layers. One pipeline.
        </h2>
        <p className="mb-12 max-w-[560px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          AgentBreeder is a layered platform. Every agent moves through all five layers in order — from how it is built to how it is governed. Click any layer to explore.
        </p>

        <div className="flex flex-col gap-2">
          {LAYERS.map((layer, idx) => {
            const isOpen = active === layer.id;
            const isLast = idx === LAYERS.length - 1;

            return (
              <div key={layer.id} className="relative">
                {/* Connector arrow between layers */}
                {!isLast && (
                  <div className="absolute left-7 -bottom-2 z-10 flex flex-col items-center" style={{ transform: 'translateX(-50%)' }}>
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                      <path d="M8 2v9M4 8l4 4 4-4" stroke={layer.color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.5" />
                    </svg>
                  </div>
                )}

                <button
                  className="w-full text-left rounded-[14px] border transition-all duration-200"
                  style={{
                    borderColor: isOpen ? layer.color + '80' : 'var(--border)',
                    background: isOpen ? layer.color + '08' : 'var(--bg-surface)',
                    boxShadow: isOpen ? `0 0 24px ${layer.color}18` : 'none',
                  }}
                  onClick={() => setActive(isOpen ? null : layer.id)}
                  aria-expanded={isOpen}
                >
                  {/* Row header */}
                  <div className="flex items-center gap-4 px-5 py-4">
                    <span
                      className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-black"
                      style={{ background: layer.color + '20', color: layer.color }}
                    >
                      {layer.number}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3">
                        <span className="text-[15px] font-bold text-white">{layer.label}</span>
                        <span
                          className="hidden sm:inline-block text-[11px] rounded-full px-2.5 py-0.5"
                          style={{ background: layer.color + '15', color: layer.color }}
                        >
                          {layer.items.length} components
                        </span>
                      </div>
                      <p className="text-[12px] mt-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
                        {layer.subtitle}
                      </p>
                    </div>
                    {/* Output pill */}
                    <div
                      className="hidden lg:flex items-center gap-1.5 rounded-full border px-3 py-1 flex-shrink-0"
                      style={{ borderColor: layer.color + '30', background: layer.color + '08' }}
                    >
                      <span className="text-[10px]" style={{ color: layer.color }}>→</span>
                      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{layer.output}</span>
                    </div>
                    {/* Chevron */}
                    <svg
                      width="16" height="16" viewBox="0 0 16 16" fill="none"
                      className="flex-shrink-0 transition-transform duration-200"
                      style={{ transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)', color: 'var(--text-dim)' }}
                      aria-hidden
                    >
                      <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>

                  {/* Expanded items */}
                  {isOpen && (
                    <div className="px-5 pb-5 pt-1 border-t" style={{ borderColor: layer.color + '25' }}>
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5 mt-3">
                        {layer.items.map((item) => (
                          <div
                            key={item.name}
                            className="rounded-xl border p-3"
                            style={{ borderColor: layer.color + '25', background: layer.color + '05' }}
                          >
                            <div className="flex items-start justify-between gap-2 mb-1.5">
                              <div className="flex items-center gap-2">
                                <span className="text-[16px]">{item.icon}</span>
                                <span className="text-[13px] font-semibold text-white">{item.name}</span>
                              </div>
                              <span
                                className="text-[9px] rounded-full px-1.5 py-0.5 flex-shrink-0 font-mono"
                                style={{ background: layer.color + '20', color: layer.color }}
                              >
                                {item.badge}
                              </span>
                            </div>
                            <p className="text-[11px] leading-[1.55]" style={{ color: 'var(--text-muted)' }}>{item.desc}</p>
                          </div>
                        ))}
                      </div>
                      {/* Output */}
                      <div
                        className="mt-4 flex items-center gap-2 rounded-lg px-3 py-2"
                        style={{ background: layer.color + '10', border: `1px dashed ${layer.color}40` }}
                      >
                        <span className="text-[12px]" style={{ color: layer.color }}>Output →</span>
                        <span className="text-[12px] font-mono" style={{ color: 'var(--text-muted)' }}>{layer.output}</span>
                      </div>
                    </div>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
