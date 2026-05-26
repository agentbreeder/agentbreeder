// Export the Cloud waitlist from Vercel KV / Upstash Redis to CSV (stdout).
//
// Usage:
//   vercel env pull .env.local          # one-time: pull KV creds locally
//   npm run waitlist:export             # prints email,signed_up_at CSV
//   npm run waitlist:export > waitlist.csv
//
// You can also read it without this script: Vercel → Storage → your KV →
// Data Browser → `ZRANGE waitlist 0 -1 REV WITHSCORES`.
import { Redis } from '@upstash/redis';

const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;

if (!url || !token) {
  console.error(
    'Missing KV credentials. Run `vercel env pull .env.local` (or copy KV_REST_API_URL ' +
      'and KV_REST_API_TOKEN from Vercel → Storage → your KV) and retry.',
  );
  process.exit(1);
}

const redis = new Redis({ url, token });
const rows = await redis.zrange('waitlist', 0, -1, { withScores: true });

process.stdout.write('email,signed_up_at\n');
for (let i = 0; i < rows.length; i += 2) {
  const email = String(rows[i]);
  const signedUpAt = new Date(Number(rows[i + 1])).toISOString();
  process.stdout.write(`${email},${signedUpAt}\n`);
}
process.stderr.write(`\n${rows.length / 2} signup(s).\n`);
