'use client';

import { useState } from 'react';

export function CloudBanner() {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;

  return (
    <div
      className="relative flex items-center justify-center gap-3 px-6 py-2.5 text-sm font-medium"
      style={{
        background: 'linear-gradient(90deg, rgba(34,197,94,0.12) 0%, rgba(167,139,250,0.12) 50%, rgba(251,146,60,0.10) 100%)',
        borderBottom: '1px solid rgba(167,139,250,0.20)',
      }}
    >
      {/* Animated shimmer */}
      <div
        className="pointer-events-none absolute inset-0 opacity-30"
        style={{
          background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.06) 40%, transparent 100%)',
          animation: 'shimmer 3s infinite linear',
        }}
      />
      <span className="relative z-10 animate-pulse text-base">⚡</span>
      <span className="relative z-10" style={{ color: 'var(--text-muted)' }}>
        <span className="font-semibold text-white">AgentBreeder Cloud</span>
        {' '}is coming —{' '}
        <span style={{ color: '#c084fc' }}>managed infrastructure, zero DevOps, automatic governance.</span>
      </span>
      <a
        href="#cloud"
        className="relative z-10 rounded-full border px-3 py-0.5 text-xs font-bold no-underline transition-all hover:opacity-80"
        style={{
          background: 'rgba(167,139,250,0.15)',
          borderColor: 'rgba(167,139,250,0.35)',
          color: '#c084fc',
        }}
      >
        Learn more ↓
      </a>
      <button
        onClick={() => setDismissed(true)}
        className="relative z-10 ml-2 rounded-full p-0.5 text-xs transition-opacity hover:opacity-70"
        style={{ color: 'var(--text-dim)' }}
        aria-label="Dismiss"
      >
        ✕
      </button>
      <style>{`
        @keyframes shimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>
    </div>
  );
}
