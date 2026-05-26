import { NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';

// Waitlist signups are stored in Vercel KV / Upstash Redis as a sorted set
// `waitlist` (member = email, score = signup timestamp → deduped + ordered).
// Required env (auto-injected by the Vercel "Upstash for Redis" integration):
//   KV_REST_API_URL + KV_REST_API_TOKEN  (UPSTASH_REDIS_REST_* also accepted).
const WAITLIST_KEY = 'waitlist';
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const RATE_LIMIT = 5; // max signups...
const RATE_WINDOW_S = 60; // ...per IP per minute

function getRedis(): Redis | null {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;
  return new Redis({ url, token });
}

export async function POST(request: Request) {
  const redis = getRedis();
  if (!redis) {
    console.error('Vercel KV / Upstash env not set — cannot store waitlist signup.');
    return NextResponse.json({ error: 'The waitlist is temporarily unavailable.' }, { status: 503 });
  }

  // Per-IP rate limit via INCR + EXPIRE (no extra dependency).
  const ip = request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'unknown';
  try {
    const rlKey = `rl:waitlist:${ip}`;
    const count = await redis.incr(rlKey);
    if (count === 1) await redis.expire(rlKey, RATE_WINDOW_S);
    if (count > RATE_LIMIT) {
      return NextResponse.json({ error: 'Too many attempts. Please try again in a minute.' }, { status: 429 });
    }
  } catch (err) {
    console.error('Waitlist rate-limit check failed', err);
    return NextResponse.json({ error: 'Could not join the waitlist. Please try again.' }, { status: 502 });
  }

  let body: { email?: unknown; company?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Invalid request body.' }, { status: 400 });
  }

  // Honeypot: real users never see/fill `company`; bots do. Accept silently, store nothing.
  if (typeof body.company === 'string' && body.company.trim() !== '') {
    return NextResponse.json({ ok: true });
  }

  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
  if (!email || email.length > 254 || !EMAIL_RE.test(email)) {
    return NextResponse.json({ error: 'Please enter a valid email address.' }, { status: 400 });
  }

  try {
    await redis.zadd(WAITLIST_KEY, { score: Date.now(), member: email });
  } catch (err) {
    console.error('Failed to store waitlist signup', err);
    return NextResponse.json({ error: 'Could not join the waitlist. Please try again.' }, { status: 502 });
  }

  return NextResponse.json({ ok: true });
}
