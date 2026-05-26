import { NextResponse } from 'next/server';

// Waitlist signups are emailed to the team via Resend (https://resend.com).
// Required env: RESEND_API_KEY. Optional: RESEND_FROM (must be a verified
// Resend sender; the agentbreeder.io domain has to be verified in Resend).
const RESEND_ENDPOINT = 'https://api.resend.com/emails';
const TO = 'hello@agentbreeder.io';
const FROM = process.env.RESEND_FROM || 'AgentBreeder Waitlist <waitlist@agentbreeder.io>';
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(request: Request) {
  let email: unknown;
  try {
    ({ email } = await request.json());
  } catch {
    return NextResponse.json({ error: 'Invalid request body.' }, { status: 400 });
  }

  if (typeof email !== 'string' || email.length > 254 || !EMAIL_RE.test(email)) {
    return NextResponse.json({ error: 'Please enter a valid email address.' }, { status: 400 });
  }

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    console.error('RESEND_API_KEY is not set — cannot send waitlist email.');
    return NextResponse.json({ error: 'The waitlist is temporarily unavailable.' }, { status: 503 });
  }

  try {
    const res = await fetch(RESEND_ENDPOINT, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: FROM,
        to: [TO],
        reply_to: email,
        subject: 'New AgentBreeder Cloud waitlist signup',
        text: `New waitlist signup for AgentBreeder Cloud:\n\n${email}\n\nReply to this email to reach them directly.`,
      }),
    });

    if (!res.ok) {
      console.error('Resend send failed', res.status, await res.text().catch(() => ''));
      return NextResponse.json({ error: 'Could not join the waitlist. Please try again.' }, { status: 502 });
    }
  } catch (err) {
    console.error('Resend request threw', err);
    return NextResponse.json({ error: 'Could not join the waitlist. Please try again.' }, { status: 502 });
  }

  return NextResponse.json({ ok: true });
}
