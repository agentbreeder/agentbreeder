'use client';

interface Player {
  name: string;
  x: number; // 0–100: portability (low=vendor-locked, high=portable)
  y: number; // 0–100: enterprise readiness (low=dev tool, high=enterprise)
  highlight?: boolean;
  labelDir?: 'top' | 'bottom' | 'left' | 'right';
}

const PLAYERS: Player[] = [
  { name: 'AgentBreeder',       x: 88, y: 88, highlight: true, labelDir: 'top' },
  { name: 'AWS Bedrock',        x: 12, y: 62, labelDir: 'left' },
  { name: 'Vertex AI (Google)', x: 16, y: 58, labelDir: 'bottom' },
  { name: 'Azure AI Studio',    x: 14, y: 54, labelDir: 'right' },
  { name: 'LangGraph',          x: 58, y: 28, labelDir: 'bottom' },
  { name: 'CrewAI',             x: 52, y: 24, labelDir: 'bottom' },
  { name: 'Mastra',             x: 62, y: 22, labelDir: 'bottom' },
  { name: 'OpenAI Agents',      x: 22, y: 34, labelDir: 'right' },
  { name: 'Google ADK',         x: 18, y: 30, labelDir: 'right' },
  { name: 'AutoGen',            x: 72, y: 20, labelDir: 'bottom' },
  { name: 'Flowise',            x: 32, y: 52, labelDir: 'right' },
  { name: 'Dify',               x: 38, y: 56, labelDir: 'top' },
  { name: 'Agent Garden',       x: 22, y: 18, labelDir: 'right' },
];

const W = 660;
const H = 500;
const PAD = 56;

function px(v: number) { return PAD + (v / 100) * (W - PAD * 2); }
function py(v: number) { return H - PAD - (v / 100) * (H - PAD * 2); }

function labelPos(p: Player) {
  const x = px(p.x);
  const y = py(p.y);
  const off = p.highlight ? 14 : 10;
  switch (p.labelDir ?? 'top') {
    case 'top':    return { tx: x, ty: y - off, anchor: 'middle' };
    case 'bottom': return { tx: x, ty: y + off + 4, anchor: 'middle' };
    case 'left':   return { tx: x - off, ty: y + 4, anchor: 'end' };
    case 'right':  return { tx: x + off, ty: y + 4, anchor: 'start' };
  }
}

export function PositioningChart() {
  return (
    <div
      className="my-8 overflow-x-auto rounded-2xl border"
      style={{ borderColor: 'rgba(255,255,255,0.10)', background: '#0d0d10' }}
    >
      {/* Header */}
      <div className="border-b px-6 py-4" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
        <p className="text-[11px] font-semibold uppercase tracking-[1.2px]" style={{ color: '#22c55e' }}>
          Market Positioning
        </p>
        <p className="mt-0.5 text-[13px]" style={{ color: '#71717a' }}>
          Portability vs Enterprise Readiness — where every major player sits
        </p>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        style={{ display: 'block', maxWidth: '100%' }}
      >
        {/* Quadrant backgrounds */}
        {/* Bottom-left: Vendor-locked dev tools */}
        <rect x={PAD} y={H / 2} width={(W - PAD * 2) / 2} height={(H - PAD * 2) / 2}
          fill="rgba(239,68,68,0.03)" />
        {/* Top-left: Enterprise but locked */}
        <rect x={PAD} y={PAD} width={(W - PAD * 2) / 2} height={(H - PAD * 2) / 2}
          fill="rgba(251,146,60,0.03)" />
        {/* Bottom-right: Portable but not enterprise */}
        <rect x={W / 2} y={H / 2} width={(W - PAD * 2) / 2} height={(H - PAD * 2) / 2}
          fill="rgba(251,146,60,0.03)" />
        {/* Top-right: Leaders */}
        <rect x={W / 2} y={PAD} width={(W - PAD * 2) / 2} height={(H - PAD * 2) / 2}
          fill="rgba(34,197,94,0.04)" />

        {/* Grid lines */}
        {[20, 40, 60, 80].map(v => (
          <g key={v}>
            <line x1={px(v)} y1={PAD} x2={px(v)} y2={H - PAD}
              stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
            <line x1={PAD} y1={py(v)} x2={W - PAD} y2={py(v)}
              stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
          </g>
        ))}

        {/* Center dividers */}
        <line x1={W / 2} y1={PAD} x2={W / 2} y2={H - PAD}
          stroke="rgba(255,255,255,0.10)" strokeWidth="1" strokeDasharray="4 4" />
        <line x1={PAD} y1={H / 2} x2={W - PAD} y2={H / 2}
          stroke="rgba(255,255,255,0.10)" strokeWidth="1" strokeDasharray="4 4" />

        {/* Axis arrows */}
        <defs>
          <marker id="arrowX" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
            <path d="M0 1.5 L4.5 3 L0 4.5" fill="none" stroke="#3f3f46" strokeWidth="1" />
          </marker>
          <marker id="arrowY" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
            <path d="M1.5 4.5 L3 0 L4.5 4.5" fill="none" stroke="#3f3f46" strokeWidth="1" />
          </marker>
        </defs>
        <line x1={PAD - 8} y1={H - PAD} x2={W - PAD + 12} y2={H - PAD}
          stroke="#3f3f46" strokeWidth="1.2" markerEnd="url(#arrowX)" />
        <line x1={PAD} y1={H - PAD + 8} x2={PAD} y2={PAD - 12}
          stroke="#3f3f46" strokeWidth="1.2" markerEnd="url(#arrowY)" />

        {/* Axis labels */}
        <text x={W / 2} y={H - 10} textAnchor="middle"
          fill="#52525b" fontSize="11" fontWeight="600" letterSpacing="0.5">
          PORTABILITY (Vendor-locked → Framework + Cloud Agnostic)
        </text>
        <text x={16} y={H / 2} textAnchor="middle" dominantBaseline="middle"
          fill="#52525b" fontSize="11" fontWeight="600" letterSpacing="0.5"
          transform={`rotate(-90, 16, ${H / 2})`}>
          ENTERPRISE READINESS (Dev Tool → Enterprise Platform)
        </text>

        {/* Quadrant labels */}
        <text x={PAD + 8} y={PAD + 16} fill="rgba(239,68,68,0.35)" fontSize="10" fontWeight="700" letterSpacing="0.8">
          LOCKED IN
        </text>
        <text x={W / 2 + 8} y={PAD + 16} fill="rgba(34,197,94,0.5)" fontSize="10" fontWeight="700" letterSpacing="0.8">
          ★ LEADERS
        </text>
        <text x={PAD + 8} y={H - PAD - 8} fill="rgba(255,255,255,0.12)" fontSize="10" fontWeight="700" letterSpacing="0.8">
          DEV TOOLS
        </text>
        <text x={W / 2 + 8} y={H - PAD - 8} fill="rgba(255,255,255,0.12)" fontSize="10" fontWeight="700" letterSpacing="0.8">
          PORTABLE
        </text>

        {/* Player dots + labels */}
        {PLAYERS.map((p) => {
          const cx = px(p.x);
          const cy = py(p.y);
          const lp = labelPos(p);
          return (
            <g key={p.name}>
              {p.highlight ? (
                <>
                  {/* glow ring */}
                  <circle cx={cx} cy={cy} r={18} fill="rgba(34,197,94,0.08)" />
                  <circle cx={cx} cy={cy} r={10}
                    fill="rgba(34,197,94,0.18)" stroke="#22c55e" strokeWidth="1.5" />
                  <circle cx={cx} cy={cy} r={4} fill="#22c55e" />
                </>
              ) : (
                <circle cx={cx} cy={cy} r={5}
                  fill="rgba(113,113,122,0.25)" stroke="rgba(255,255,255,0.15)" strokeWidth="1" />
              )}
              <text
                x={lp.tx} y={lp.ty}
                textAnchor={lp.anchor as 'middle' | 'start' | 'end'}
                fill={p.highlight ? '#22c55e' : '#a1a1aa'}
                fontSize={p.highlight ? 12 : 10}
                fontWeight={p.highlight ? '700' : '500'}
              >
                {p.name}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-6 border-t px-6 py-3" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
        <span className="inline-flex items-center gap-2 text-[11px]" style={{ color: '#22c55e' }}>
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#22c55e]" />
          AgentBreeder
        </span>
        <span className="inline-flex items-center gap-2 text-[11px]" style={{ color: '#71717a' }}>
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: 'rgba(255,255,255,0.25)' }} />
          Other players
        </span>
        <span className="ml-auto text-[11px]" style={{ color: '#52525b' }}>
          Position reflects capability scope, not market share
        </span>
      </div>
    </div>
  );
}
