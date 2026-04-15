'use client';

import { type ReactNode, useEffect, useRef } from 'react';

type TabId = 'prompts' | 'tools' | 'kb' | 'mcp';

const TABS: Array<{ id: TabId; label: string; color: string }> = [
  { id: 'prompts', label: 'Prompts', color: '#58a6ff' },
  { id: 'tools', label: 'Tools', color: '#3fb950' },
  { id: 'kb', label: 'Knowledge Bases', color: '#a78bfa' },
  { id: 'mcp', label: 'MCP Servers', color: '#ff9900' },
];

const TAB_IDS: TabId[] = ['prompts', 'tools', 'kb', 'mcp'];

const STEP_DATA: Record<TabId, Array<{ label: string; detail: string; api: string }>> = {
  prompts: [
    { label: 'Author', detail: 'Write text with {{variable}} placeholders', api: '' },
    { label: 'Register', detail: 'Store name, version, content, team in the registry', api: 'POST /registry/prompts' },
    { label: 'Test', detail: 'Render variables · token estimate · response preview', api: 'POST /prompts/test' },
    { label: 'Version', detail: 'Auto-snapshot + unified diff on every content update', api: 'PUT .../content → snapshot' },
    { label: 'Use in Agent', detail: 'Resolves the latest version from registry at deploy time', api: 'prompts.system: prompts/name' },
  ],
  tools: [
    { label: 'Define', detail: 'Python function or API endpoint with JSON Schema', api: '' },
    { label: 'Register', detail: 'Store name, schema, type, endpoint in the registry', api: 'POST /registry/tools' },
    { label: 'Sandbox', detail: 'Execute in Docker isolation — 256MB, no network by default', api: 'POST /tools/sandbox/execute' },
    { label: 'Discover Usage', detail: 'See every agent that references this tool org-wide', api: 'GET /registry/tools/{id}/usage' },
    { label: 'Use in Agent', detail: 'Reference by registry path — injected at deploy time', api: 'tools: - ref: tools/name' },
  ],
  kb: [
    { label: 'Create Index', detail: 'Configure embedding model, chunk size, and strategy', api: 'POST /rag/indexes' },
    { label: 'Ingest', detail: 'Upload PDF, MD, CSV, JSON — auto-chunked and embedded', api: 'POST /rag/indexes/{id}/ingest' },
    { label: 'Embed & Store', detail: 'text-embedding-3-small vectors stored per chunk', api: '' },
    { label: 'Search', detail: 'Hybrid vector + full-text (70/30 weight) · top-k retrieval', api: 'POST /rag/search' },
    { label: 'Use in Agent', detail: 'Auto-queried on relevant messages at runtime', api: 'knowledge_bases: - ref: kb/name' },
  ],
  mcp: [
    { label: 'Build', detail: 'Decorate Python functions with @mcp.tool() via FastMCP', api: '' },
    { label: 'Scan', detail: 'Auto-discover from .mcp.json config and running ports', api: 'agentbreeder scan' },
    { label: 'Register', detail: 'Auto-added to tool registry on scan, or register manually', api: 'POST /mcp-servers' },
    { label: 'Test', detail: 'Probe connectivity · list tools · sample call execution', api: 'POST /mcp-servers/{id}/test' },
    { label: 'Use in Agent', detail: 'Sidecar container injected at deploy — stdio or SSE', api: 'tools: - ref: mcp-servers/name' },
  ],
};

const TITLES: Record<TabId, string> = {
  prompts: 'Prompt Lifecycle',
  tools: 'Tool Lifecycle',
  kb: 'Knowledge Base Lifecycle',
  mcp: 'MCP Server Lifecycle',
};

const STEP_MS = 1800;
const HOLD_MS = 1600;
const INIT_DELAY = 700;

// ── Sub-components (static JSX, no innerHTML) ──────────────────────────────

type YamlLineType = 'normal' | 'key' | 'highlight' | 'comment';

const YAML_COLORS: Record<YamlLineType, string> = {
  normal: '#8b949e',
  key: '#e6edf3',
  highlight: '#79c0ff',
  comment: '#484f58',
};

function YamlLine({ text, type }: { text: string; type: YamlLineType }) {
  return (
    <div>
      <span style={{ color: YAML_COLORS[type] }}>{text || '\u00A0'}</span>
      {type === 'highlight' && (
        <span style={{ color: '#3fb950', marginLeft: 10, fontSize: 10, opacity: 0.85 }}>
          ← registry ref
        </span>
      )}
    </div>
  );
}

function YamlBlock({ lines }: { lines: Array<{ text: string; type: YamlLineType }> }) {
  return (
    <pre
      style={{
        background: '#0d1117',
        border: '1px solid #21262d',
        borderRadius: 8,
        padding: '14px 16px',
        fontSize: 12,
        lineHeight: 1.75,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        margin: 0,
        overflow: 'hidden',
      }}
    >
      {lines.map((line, i) => (
        <YamlLine key={i} text={line.text} type={line.type} />
      ))}
    </pre>
  );
}

function ApiNote({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        marginTop: 10,
        padding: '7px 12px',
        background: '#0d2818',
        border: '1px solid #1a4a2e',
        borderRadius: 6,
        fontSize: 11,
        color: '#3fb950',
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        letterSpacing: '0.01em',
      }}
    >
      {children}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function RegistryLifecycle() {
  const tabBtnRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const stepRowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const stepCircleRefs = useRef<(HTMLDivElement | null)[]>([]);
  const stepLabelRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const stepDetailRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const stepApiRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const yamlPanelRefs = useRef<(HTMLDivElement | null)[]>([]);
  const titleRef = useRef<HTMLParagraphElement | null>(null);
  const progressFillRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let running = true;
    let tabIdx = 0;
    let stepIdx = -1;
    let stepTimer: ReturnType<typeof setTimeout>;
    let holdTimer: ReturnType<typeof setTimeout>;

    function resetForTab(ti: number) {
      const tab = TABS[ti];
      const id = TAB_IDS[ti];

      if (titleRef.current) titleRef.current.textContent = TITLES[id];

      TABS.forEach((t, i) => {
        const btn = tabBtnRefs.current[i];
        if (!btn) return;
        btn.style.color = i === ti ? t.color : '#484f58';
        btn.style.borderBottomColor = i === ti ? t.color : 'transparent';
      });

      for (let s = 0; s < 5; s++) {
        const circle = stepCircleRefs.current[s];
        if (circle) {
          circle.style.background = '#161b22';
          circle.style.borderColor = '#30363d';
          circle.style.boxShadow = 'none';
        }
        const row = stepRowRefs.current[s];
        if (row) row.style.opacity = '0.3';

        const step = STEP_DATA[id][s];
        const labelEl = stepLabelRefs.current[s];
        const detailEl = stepDetailRefs.current[s];
        const apiEl = stepApiRefs.current[s];
        if (labelEl) labelEl.textContent = step.label;
        if (detailEl) detailEl.textContent = step.detail;
        if (apiEl) {
          apiEl.textContent = step.api;
          apiEl.style.display = step.api ? 'inline-block' : 'none';
        }
      }

      yamlPanelRefs.current.forEach((panel, i) => {
        if (panel) panel.style.display = i === ti ? 'block' : 'none';
      });

      const fill = progressFillRef.current;
      if (fill) {
        fill.style.transition = 'none';
        fill.style.width = '0%';
        void fill.offsetWidth; // force reflow
      }
    }

    function activateStep(si: number) {
      const tab = TABS[tabIdx];
      const circle = stepCircleRefs.current[si];
      if (circle) {
        circle.style.background = tab.color;
        circle.style.borderColor = tab.color;
        circle.style.boxShadow = `0 0 10px ${tab.color}44`;
      }
      const row = stepRowRefs.current[si];
      if (row) row.style.opacity = '1';

      const fill = progressFillRef.current;
      if (fill) {
        const pct = ((si + 1) / 5) * 100;
        fill.style.transition = `width ${STEP_MS * 0.75}ms ease`;
        fill.style.width = `${pct}%`;
        fill.style.background = tab.color;
      }
    }

    function runStep() {
      if (!running) return;
      stepIdx++;
      if (stepIdx < 5) {
        activateStep(stepIdx);
        stepTimer = setTimeout(runStep, STEP_MS);
      } else {
        holdTimer = setTimeout(() => {
          if (!running) return;
          tabIdx = (tabIdx + 1) % 4;
          stepIdx = -1;
          resetForTab(tabIdx);
          stepTimer = setTimeout(runStep, 500);
        }, HOLD_MS);
      }
    }

    resetForTab(0);
    stepTimer = setTimeout(runStep, INIT_DELAY);

    return () => {
      running = false;
      clearTimeout(stepTimer);
      clearTimeout(holdTimer);
    };
  }, []);

  return (
    <section style={{ background: '#010409', padding: '88px 24px', borderTop: '1px solid #21262d' }}>
      <div style={{ maxWidth: 920, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: 52 }}>
          <p style={{
            fontSize: 12,
            fontWeight: 600,
            color: '#58a6ff',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            marginBottom: 14,
          }}>
            Registry
          </p>
          <h2 style={{
            fontSize: 'clamp(26px, 4vw, 38px)',
            fontWeight: 700,
            color: '#e6edf3',
            marginBottom: 14,
            letterSpacing: '-0.5px',
            lineHeight: 1.2,
          }}>
            Build once. Reference everywhere.
          </h2>
          <p style={{ color: '#8b949e', fontSize: 15, maxWidth: 580, margin: '0 auto', lineHeight: 1.6 }}>
            Prompts, tools, knowledge bases, and MCP servers live in a shared org registry.
            Define once — wire into any agent, any framework, any cloud.
          </p>
        </div>

        {/* Panel */}
        <div style={{
          background: '#0d1117',
          border: '1px solid #30363d',
          borderRadius: 12,
          overflow: 'hidden',
        }}>

          {/* Tab bar */}
          <div style={{
            display: 'flex',
            borderBottom: '1px solid #21262d',
            background: '#161b22',
            padding: '0 4px',
            overflowX: 'auto',
          }}>
            {TABS.map((tab, i) => (
              <button
                key={tab.id}
                ref={el => { tabBtnRefs.current[i] = el; }}
                style={{
                  background: 'none',
                  border: 'none',
                  borderBottom: `2px solid ${i === 0 ? tab.color : 'transparent'}`,
                  padding: '13px 20px',
                  fontSize: 13,
                  fontWeight: 500,
                  color: i === 0 ? tab.color : '#484f58',
                  cursor: 'default',
                  transition: 'color 0.35s, border-color 0.35s',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Grid: steps | yaml */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)',
            minHeight: 340,
          }}>

            {/* Left — lifecycle steps */}
            <div style={{ padding: '28px 28px 20px', borderRight: '1px solid #21262d' }}>
              <p
                ref={titleRef}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: '#484f58',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  marginBottom: 22,
                }}
              >
                Prompt Lifecycle
              </p>

              {[0, 1, 2, 3, 4].map(s => (
                <div
                  key={s}
                  ref={el => { stepRowRefs.current[s] = el; }}
                  style={{
                    display: 'flex',
                    gap: 14,
                    opacity: 0.3,
                    transition: 'opacity 0.45s ease',
                  }}
                >
                  {/* Circle + connector */}
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
                    <div
                      ref={el => { stepCircleRefs.current[s] = el; }}
                      style={{
                        width: 26,
                        height: 26,
                        borderRadius: '50%',
                        border: '2px solid #30363d',
                        background: '#161b22',
                        transition: 'background 0.45s, border-color 0.45s, box-shadow 0.45s',
                        flexShrink: 0,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <span style={{ fontSize: 10, color: '#e6edf3', fontWeight: 700 }}>{s + 1}</span>
                    </div>
                    {s < 4 && (
                      <div style={{
                        width: 1,
                        height: 34,
                        background: 'linear-gradient(180deg, #30363d 0%, #21262d 100%)',
                      }} />
                    )}
                  </div>

                  {/* Label + detail + api badge */}
                  <div style={{ paddingBottom: s < 4 ? 8 : 0, paddingTop: 2 }}>
                    <span
                      ref={el => { stepLabelRefs.current[s] = el; }}
                      style={{ display: 'block', fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 2 }}
                    />
                    <span
                      ref={el => { stepDetailRefs.current[s] = el; }}
                      style={{ display: 'block', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}
                    />
                    <span
                      ref={el => { stepApiRefs.current[s] = el; }}
                      style={{
                        display: 'none',
                        fontSize: 10,
                        color: '#3fb950',
                        fontFamily: "'JetBrains Mono', monospace",
                        marginTop: 5,
                        background: '#0d2818',
                        padding: '2px 7px',
                        borderRadius: 4,
                        border: '1px solid #1a4a2e',
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Right — agent.yaml panels */}
            <div style={{ padding: '28px 24px 20px' }}>
              <p style={{
                fontSize: 11,
                fontWeight: 600,
                color: '#484f58',
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                marginBottom: 14,
              }}>
                agent.yaml
              </p>

              {/* Prompts */}
              <div ref={el => { yamlPanelRefs.current[0] = el; }} style={{ display: 'block' }}>
                <YamlBlock lines={[
                  { text: 'name: support-agent', type: 'normal' },
                  { text: 'framework: langgraph', type: 'normal' },
                  { text: '', type: 'normal' },
                  { text: 'prompts:', type: 'key' },
                  { text: '  system: prompts/support-v2', type: 'highlight' },
                  { text: '  # or inline:', type: 'comment' },
                  { text: "  # system: 'You are a {{role}}'", type: 'comment' },
                ]} />
                <ApiNote>GET /registry/prompts/{'{id}'}/versions/history/diff — compare any two versions</ApiNote>
              </div>

              {/* Tools */}
              <div ref={el => { yamlPanelRefs.current[1] = el; }} style={{ display: 'none' }}>
                <YamlBlock lines={[
                  { text: 'name: support-agent', type: 'normal' },
                  { text: 'framework: openai_agents', type: 'normal' },
                  { text: '', type: 'normal' },
                  { text: 'tools:', type: 'key' },
                  { text: '  - ref: tools/zendesk-mcp', type: 'highlight' },
                  { text: '  - ref: tools/order-lookup', type: 'highlight' },
                  { text: '  # or inline: name, type, schema', type: 'comment' },
                ]} />
                <ApiNote>POST /tools/sandbox/execute — test before wiring to agent</ApiNote>
              </div>

              {/* KB */}
              <div ref={el => { yamlPanelRefs.current[2] = el; }} style={{ display: 'none' }}>
                <YamlBlock lines={[
                  { text: 'name: research-agent', type: 'normal' },
                  { text: 'framework: claude_sdk', type: 'normal' },
                  { text: '', type: 'normal' },
                  { text: 'knowledge_bases:', type: 'key' },
                  { text: '  - ref: kb/product-docs', type: 'highlight' },
                  { text: '  - ref: kb/return-policy', type: 'highlight' },
                  { text: '# queried automatically at runtime', type: 'comment' },
                ]} />
                <ApiNote>POST /rag/search — hybrid vector + text, configurable weights</ApiNote>
              </div>

              {/* MCP */}
              <div ref={el => { yamlPanelRefs.current[3] = el; }} style={{ display: 'none' }}>
                <YamlBlock lines={[
                  { text: 'name: support-agent', type: 'normal' },
                  { text: 'framework: langgraph', type: 'normal' },
                  { text: '', type: 'normal' },
                  { text: 'tools:', type: 'key' },
                  { text: '  - ref: mcp-servers/zendesk', type: 'highlight' },
                  { text: '  - ref: mcp-servers/slack', type: 'highlight' },
                  { text: '# sidecar injected at deploy time', type: 'comment' },
                ]} />
                <ApiNote>POST /mcp-servers/{'{id}'}/discover — enumerate available tools</ApiNote>
              </div>
            </div>
          </div>

          {/* Progress bar */}
          <div style={{ height: 2, background: '#21262d' }}>
            <div
              ref={progressFillRef}
              style={{ height: '100%', width: '0%', background: '#58a6ff', transition: 'none' }}
            />
          </div>
        </div>

        {/* Bottom stat row */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 16,
          marginTop: 28,
        }}>
          {[
            { label: 'Prompts', detail: 'Versioned · diffable · testable', color: '#58a6ff' },
            { label: 'Tools', detail: 'Sandboxed · schema-validated', color: '#3fb950' },
            { label: 'Knowledge Bases', detail: 'Hybrid search · auto-chunked', color: '#a78bfa' },
            { label: 'MCP Servers', detail: 'Auto-discovered · sidecar-deployed', color: '#ff9900' },
          ].map(item => (
            <div
              key={item.label}
              style={{
                background: '#0d1117',
                border: '1px solid #21262d',
                borderRadius: 8,
                padding: '14px 16px',
              }}
            >
              <p style={{ fontSize: 12, fontWeight: 600, color: item.color, marginBottom: 4 }}>{item.label}</p>
              <p style={{ fontSize: 11, color: '#484f58', lineHeight: 1.4 }}>{item.detail}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
