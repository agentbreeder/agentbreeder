'use client';

import { useState } from 'react';

export function WaitlistForm() {
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'sent' | 'error'>('idle');
  const [error, setError] = useState('');
  const loading = status === 'loading';

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email || loading) return;
    const company = (e.currentTarget.elements.namedItem('company') as HTMLInputElement | null)?.value ?? '';
    setStatus('loading');
    setError('');
    try {
      const res = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, company }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(data.error || 'Something went wrong. Please try again.');
      }
      setStatus('sent');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
    }
  }

  return (
    <div id="waitlist">
      {status === 'sent' ? (
        <div role="status" aria-live="polite">
          <span
            className="inline-flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-semibold"
            style={{ background: 'rgba(167,139,250,0.12)', color: '#c084fc', border: '1px solid rgba(167,139,250,0.25)' }}
          >
            ✓ You&rsquo;re on the list — we&rsquo;ll email you the moment access opens.
          </span>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          {/* Honeypot — hidden from users; bots that fill it are silently dropped. */}
          <input type="text" name="company" tabIndex={-1} autoComplete="off" aria-hidden="true" className="hidden" />
          <input
            type="email"
            required
            disabled={loading}
            aria-label="Email address"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-xl border px-4 py-3 text-sm outline-none transition-all focus:border-[#a78bfa] w-full sm:w-[260px]"
            style={{
              background: 'var(--bg-surface)',
              borderColor: 'var(--border)',
              color: 'var(--text)',
            }}
          />
          <button
            type="submit"
            disabled={loading}
            className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl border-0 px-7 py-3 text-sm font-bold transition-all hover:scale-[1.02] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            style={{
              background: 'linear-gradient(135deg, #a78bfa 0%, #f472b6 100%)',
              color: '#fff',
              boxShadow: '0 0 40px rgba(167,139,250,0.30)',
            }}
          >
            {loading ? 'Joining…' : '⚡ Join the waitlist'}
          </button>
        </form>
      )}
      {status === 'error' && (
        <p role="alert" className="mt-3 text-xs" style={{ color: '#f87171' }}>
          {error}
        </p>
      )}
      <p className="mt-3 text-xs" style={{ color: 'var(--text-dim)' }}>
        Early access opens Q3 2026 · No credit card required
      </p>
    </div>
  );
}
