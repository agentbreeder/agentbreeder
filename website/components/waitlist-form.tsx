'use client';

import { useState } from 'react';
import { CheckIcon, CircleAlertIcon } from './icons';

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
    <div>
      {status === 'sent' ? (
        <p role="status" aria-live="polite" className="inline-flex items-center gap-2 text-sm font-medium text-foreground">
          <CheckIcon size={16} />
          You&rsquo;re on the list. We&rsquo;ll write when there is something real to try.
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
          {/* Honeypot — hidden from users; bots that fill it are silently dropped. */}
          <input type="text" name="company" tabIndex={-1} autoComplete="off" aria-hidden="true" className="hidden" />
          <label htmlFor="waitlist-email" className="sr-only">
            Email address
          </label>
          <input
            id="waitlist-email"
            type="email"
            required
            disabled={loading}
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={status === 'error' ? true : undefined}
            aria-describedby={status === 'error' ? 'waitlist-error' : undefined}
            className="ab-input w-full sm:w-[280px]"
          />
          <button type="submit" disabled={loading} aria-busy={loading} className="ab-btn ab-btn-primary">
            {loading ? 'Joining…' : 'Join the waitlist'}
          </button>
        </form>
      )}
      {status === 'error' && (
        <p id="waitlist-error" role="alert" className="mt-3 inline-flex items-center gap-1.5 text-[13px] text-destructive">
          <CircleAlertIcon size={14} />
          {error}
        </p>
      )}
      <p className="mt-3 text-[13px] text-muted-foreground">No launch date yet. We only email about AgentBreeder Cloud.</p>
    </div>
  );
}
